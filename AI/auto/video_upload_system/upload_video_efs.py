import os
import time
import json
import hashlib
import threading
from pathlib import Path
from typing import Set
import paramiko
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# # ===== 사용자 설정 =====
# HOST = "s-xxxxxxxx.server.transfer.ap-northeast-2.amazonaws.com"  # Transfer Family SFTP 엔드포인트
# PORT = 22
# USER = "comfyuser"                                                # Transfer Family 사용자
# KEY_PATH = r"C:\path\to\id_rsa"                                   # 개인키 경로(또는 .ppk 변환 X, OpenSSH 키 권장)
# LOCAL_DIR = Path(r"D:\ComfyUI\ComfyUI\output")                     # ComfyUI 출력 폴더
# REMOTE_DIR = "/comfyui"                                            # EFS의 업로드 대상 디렉토리(홈디렉토리와 일치 권장)
# ALLOW_EXT = {".mp4", ".mov"}                                       # 필요 시 확장자 추가
# STATE_FILE = Path("./uploaded_state.json")                         # 업로드 이력 저장

HOST = "127.0.0.1"
PORT = 2222
USER = "foo"
KEY_PATH = r"C:\Users\<내계정>\.ssh\id_ed25519_local_sftp"  # 개인키(.pub 아님)
REMOTE_DIR = "/upload"                                      # 컨테이너 내부 경로
LOCAL_DIR  = r"D:\ComfyUI\ComfyUI\output"                   # ComfyUI 출력 폴더(예시)


# ===== 유틸 =====
LOCK = threading.Lock()
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
    client.connect(HOST, PORT, USER, key_filename=KEY_PATH, timeout=30)
    sftp = client.open_sftp()
    return client, sftp

def ensure_remote_dir(sftp, remote_dir: str):
    """리모트 디렉터리(단일 레벨) 생성 보조"""
    try:
        sftp.listdir(remote_dir)
    except IOError:
        sftp.mkdir(remote_dir)

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
            remote_path = f"{REMOTE_DIR}/{path.name}"

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

            # 상태 갱신
            STATE["uploaded"][key] = digest
            save_state(STATE)
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
