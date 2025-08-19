import os
import time
import json
import uuid
import hashlib
import threading
from pathlib import Path
from typing import Dict, Optional, Set, Any

import requests  # FastAPI가 아니라 표준 requests를 임포트해야 합니다.
import paramiko
from fastapi import FastAPI, HTTPException
from pydantic import Field, BaseModel
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# =============================================================================
# 사용자 설정
# =============================================================================
# (필요 시 Transfer Family용 설정으로 바꿔서 사용하세요)

HOST = "127.0.0.1"
PORT = 2222
USER = "foo"
KEY_PATH = r"C:\Users\<내계정>\.ssh\id_ed25519_local_sftp"  # 개인키(.pub 아님)
REMOTE_DIR = "/upload"                                      # 컨테이너/서버 내부 경로
LOCAL_DIR  = Path(r"D:\ComfyUI\ComfyUI\output")             # 반드시 Path 타입로 유지
ALLOW_EXT = {".mp4", ".mov"}                                # 필요 시 확장자 추가
STATE_FILE = Path("./uploaded_state.json")                  # 업로드 이력 저장

# === 웹훅/URL/자동알림 (옵션) ===
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")                  # ex) http://localhost:8080/api/video-callback
MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", "")            # ex) http://localhost:8000/media
AUTO_NOTIFY = os.getenv("AUTO_NOTIFY", "false").lower() == "true"

# =============================================================================
# 유틸 & 전역 상태
# =============================================================================
LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
inflight: Set[Path] = set()

def load_state():
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"uploaded": {}}

def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_FILE)

STATE = load_state()

def sha256sum(path: Path, block=1024 * 1024):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def is_file_stable(path: Path, min_stable_sec=3.0, probe_interval=1.0) -> bool:
    """파일 크기가 min_stable_sec 동안 변하지 않으면 안정으로 판단"""
    if not path.exists():
        return False
    last_size = -1
    stable_time = 0.0
    while stable_time < min_stable_sec:
        if not path.exists():
            return False
        sz = path.stat().st_size
        if sz == last_size and sz > 0:
            stable_time += probe_interval
        else:
            stable_time = 0.0
            last_size = sz
        time.sleep(probe_interval)
    # 추가로 잠금 여부 확인 시도(읽기 가능 체크)
    try:
        with path.open("rb"):
            pass
    except Exception:
        return False
    return True

def sftp_connect():
    """Paramiko SFTP 연결 생성"""
    client = paramiko.SSHClient()
    # 실서비스에선 known_hosts에 서버 호스트키를 등록하고 StrictPolicy 사용 권장
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=HOST, port=PORT, username=USER, key_filename=KEY_PATH, timeout=30)
    sftp = client.open_sftp()
    return client, sftp

def ensure_remote_dir(sftp, remote_dir: str):
    """리모트 디렉터리(다단계) 생성 보강"""
    if not remote_dir or remote_dir == "/":
        return
    parts = [p for p in remote_dir.split("/") if p]
    cur = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        cur = f"{cur}/{part}" if cur else part
        try:
            sftp.listdir(cur)
        except IOError:
            sftp.mkdir(cur)

# === 파일 URL (정적서버가 있다면 노출) ===
def build_file_url(file_name: str) -> Optional[str]:
    if not MEDIA_BASE_URL:
        return None
    return f"{MEDIA_BASE_URL.rstrip('/')}/{file_name}"

# === 이벤트 ID 생성 ===
def new_event_id(prefix: str = "video_") -> str:
    # RFC 4122 기반 난수형 UUIDv4
    return f"{prefix}{uuid.uuid4()}"

# === 완료 알림(웹훅) ===
def notify_complete(local_path: Path, remote_path: str,
                    extra: Optional[Dict[str, Any]] = None,
                    event_id: Optional[str] = None) -> Dict[str, Any]:
    """완료 메시지(경로 포함) 전송; WEBHOOK_URL 없으면 페이로드만 반환"""
    st = local_path.stat()
    eid = event_id or new_event_id()

    payload = {
        "event": "video.completed",
        "event_id": eid,                           # 고유 ID
        "file_name": local_path.name,
        "local_path": str(local_path.resolve()),
        "remote_path": remote_path,                # 예: /upload/xxx.mp4
        "file_url": build_file_url(local_path.name),
        "size": st.st_size,
        "sha256": sha256sum(local_path),
        "mtime": st.st_mtime,
        "ts": time.time(),
    }
    if extra:
        payload["extra"] = extra

    status = None
    if WEBHOOK_URL:
        try:
            # 수신측이 멱등 처리할 수 있도록 Idempotency-Key 헤더에 동일 값 제공
            headers = {"Idempotency-Key": eid}
            r = requests.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10)
            status = r.status_code
            r.raise_for_status()
        except requests.RequestException as e:
            # 실패는 예외로 올려서 502 반환하도록
            raise HTTPException(status_code=502, detail=f"Webhook error: {e}")

    return {"ok": True, "webhook_url": WEBHOOK_URL or None, "webhook_status": status, "payload": payload}

def upload_file(path: Path):
    if path.suffix.lower() not in ALLOW_EXT:
        return
    if not path.exists():
        return

    with LOCK:
        if path in inflight:
            return
        inflight.add(path)

    try:
        # 파일 안정 대기
        if not is_file_stable(path):
            print(f"[SKIP] 아직 쓰는 중인 파일: {path}")
            return

        # 중복 업로드 방지 체크
        digest = sha256sum(path)
        key = str(path.resolve())
        if STATE["uploaded"].get(key) == digest:
            print(f"[SKIP] 이미 업로드된 파일(해시 일치): {path.name}")
            return

        print(f"[INFO] 업로드 시작: {path.name}")
        client, sftp = sftp_connect()
        try:
            ensure_remote_dir(sftp, REMOTE_DIR)
            remote_path = f"{REMOTE_DIR.rstrip('/')}/{path.name}"

            # 진행 콜백
            total = path.stat().st_size
            last_pct = -1

            def cb(transferred, total_bytes):
                nonlocal last_pct
                pct = int(transferred * 100 / total_bytes) if total_bytes else 100
                if pct != last_pct:
                    print(f"\r  -> {pct}% ({transferred}/{total_bytes} bytes)", end="")
                    last_pct = pct

            sftp.put(str(path), remote_path, callback=cb)
            print("\n[OK] 업로드 완료:", path.name)

            # 상태 갱신(동시성 보호)
            with STATE_LOCK:
                STATE["uploaded"][key] = digest
                save_state(STATE)

            # === 자동 완료 알림 ===
            if AUTO_NOTIFY:
                try:
                    res = notify_complete(path, remote_path)
                    print("[OK] 완료 웹훅 전송:", res.get("webhook_status"))
                except HTTPException as e:
                    print("[WARN] 완료 웹훅 실패:", e.detail)

        finally:
            sftp.close()
            client.close()
    except Exception as e:
        print("[ERR] 업로드 실패:", path.name, "-", e)
    finally:
        with LOCK:
            inflight.discard(path)

# 초기 일괄 업로드(이미 존재하는 파일)
def upload_backlog():
    for p in sorted(LOCAL_DIR.glob("*")):
        if p.suffix.lower() in ALLOW_EXT:
            upload_file(p)

# watchdog 이벤트 핸들러
class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in ALLOW_EXT:
            # 약간의 지연 후 업로드(쓰기 완료 대기)
            threading.Thread(target=lambda: (time.sleep(1), upload_file(p)), daemon=True).start()

    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in ALLOW_EXT:
            threading.Thread(target=lambda: (time.sleep(1), upload_file(p)), daemon=True).start()

# =============================================================================
# FastAPI (완료 메시지/경로를 외부로 보내는 API) - 업로드 자체는 하지 않음
# =============================================================================
app = FastAPI(title="Uploader + Completion API")

class CompleteAPIRequest(BaseModel):
    file_path: Optional[str] = Field(None, description="로컬 파일 경로 (미지정 시 최신 영상 자동 선택)")
    remote_dir: Optional[str] = Field(None, description=f"원격 디렉터리(기본: {REMOTE_DIR})")
    file_name: Optional[str] = Field(None, description="원격 저장 파일명(기본: 로컬 파일명)")
    extra: Optional[Dict[str, Any]] = Field(None, description="추가 전송 메타데이터")
    event_id: Optional[str] = Field(None, description="클라이언트가 지정하는 이벤트 ID (선택)")

class CompleteAPIResponse(BaseModel):
    ok: bool
    webhook_url: Optional[str] = None
    webhook_status: Optional[int] = None
    payload: Dict[str, Any]

def _pick_latest_video() -> Path:
    candidates = [p for p in LOCAL_DIR.glob("*") if p.suffix.lower() in ALLOW_EXT and p.is_file()]
    if not candidates:
        raise HTTPException(status_code=404, detail="No video files found in LOCAL_DIR")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

@app.post("/api/upload/complete", response_model=CompleteAPIResponse)
def api_upload_complete(req: CompleteAPIRequest):
    # 로컬 파일 경로 결정
    if req.file_path:
        local_path = Path(req.file_path)
        if not local_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {local_path}")
    else:
        local_path = _pick_latest_video()

    # 원격 경로 결정(업로드 완료 가정)
    remote_dir = (req.remote_dir or REMOTE_DIR).rstrip("/")
    file_name = req.file_name or local_path.name
    remote_path = f"{remote_dir}/{file_name}"

    # 완료 알림 전송 (외부에서 event_id 제공 시 사용)
    result = notify_complete(local_path, remote_path, extra=req.extra, event_id=req.event_id)
    return CompleteAPIResponse(**result)

# =============================================================================
# 감시 워커 실행
# =============================================================================
def main():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    print("[INFO] 감시 폴더:", LOCAL_DIR)
    print("[INFO] 원격 폴더:", REMOTE_DIR)
    upload_backlog()

    observer = Observer()
    observer.schedule(Handler(), str(LOCAL_DIR), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
