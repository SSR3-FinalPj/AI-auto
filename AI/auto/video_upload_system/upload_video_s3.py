# -*- coding: utf-8 -*-
import os
import time
import json
import hashlib
import threading
from pathlib import Path
from typing import Set, Optional
import mimetypes

import boto3
from boto3.s3.transfer import TransferConfig
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ===== 사용자 설정 =====
S3_BUCKET   = os.getenv("S3_BUCKET",   "YOUR_BUCKET_NAME")        # ★ 바꾸세요
S3_PREFIX   = os.getenv("S3_PREFIX",   "comfyui/outputs")         # 예: "comfyui/outputs" (끝에 슬래시 X)
LOCAL_DIR   = Path(os.getenv("LOCAL_DIR", r"D:\ComfyUI\ComfyUI\output"))

# 업로드 확장자: 영상 위주. 프레임도 올리려면 ".png" 추가
ALLOW_EXT = {".mp4", ".mov"}   # 필요 시 {".mp4", ".mov", ".png"}

# 업로드 이력 파일(중복 방지)
STATE_FILE = Path(os.getenv("STATE_FILE", "./uploaded_state.json"))

# 전송 설정(멀티파트 임계값/청크/동시성)
MB = 1024 * 1024
TRANSFER_CFG = TransferConfig(
    multipart_threshold=32 * MB,   # 32MB 초과 시 멀티파트
    multipart_chunksize=16 * MB,   # 파트 크기
    max_concurrency=8,             # 동시 업로드 스레드
    use_threads=True
)
# 참고: upload_file()는 기본적으로 파일 크기가 임계값을 넘으면 멀티파트 전송으로 자동 전환됩니다. :contentReference[oaicite:3]{index=3}

# (선택) 서버측 암호화(SSE). KMS 키를 쓰려면 다음 주석을 해제하고 키 ID를 넣으세요.
EXTRA_ARGS = {
    # "ServerSideEncryption": "aws:kms",
    # "SSEKMSKeyId": "arn:aws:kms:ap-northeast-2:123456789012:key/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    # "BucketKeyEnabled": True,  # KMS 비용 최적화(S3 Bucket Key)
}
# SSE 파라미터는 S3 PutObject/업로드에서 ExtraArgs로 전달 가능합니다. :contentReference[oaicite:4]{index=4}

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
    """파일 크기가 일정 시간 변하지 않으면 완료로 간주"""
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
    try:
        with path.open("rb"):
            pass
    except Exception:
        return False
    return True

# 진행률 콜백
class Progress:
    def __init__(self, filesize: int, name: str):
        self.total = filesize
        self.seen = 0
        self.name = name
        self.last_pct = -1
        self.lock = threading.Lock()
    def __call__(self, bytes_amount):
        with self.lock:
            self.seen += bytes_amount
            pct = int(self.seen * 100 / self.total) if self.total else 100
            if pct != self.last_pct:
                print(f"\r  -> {pct}% ({self.seen}/{self.total} bytes) {self.name}", end="")
                self.last_pct = pct

def guess_content_type(path: Path) -> Optional[str]:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype

def s3_key_for(path: Path) -> str:
    # S3 “폴더”는 prefix로 표현: prefix/파일명
    return f"{S3_PREFIX.rstrip('/')}/{path.name}"

def upload_to_s3(path: Path):
    ext = path.suffix.lower()
    if ext not in ALLOW_EXT:
        return
    if not path.exists():
        return

    with LOCK:
        if path in inflight:
            return
        inflight.add(path)

    try:
        if not is_file_stable(path):
            print(f"[SKIP] 아직 쓰는 중인 파일: {path.name}")
            return

        digest = sha256sum(path)
        key_local = str(path.resolve())
        if STATE["uploaded"].get(key_local) == digest:
            print(f"[SKIP] 이미 업로드됨(해시 일치): {path.name}")
            return

        s3 = boto3.client("s3")
        key = s3_key_for(path)
        size = path.stat().st_size
        extra = dict(EXTRA_ARGS)  # 사본
        ctype = guess_content_type(path)
        if ctype:
            extra["ContentType"] = ctype   # 예: video/mp4, video/quicktime, image/png

        print(f"[INFO] 업로드 시작: s3://{S3_BUCKET}/{key}")
        progress = Progress(size, path.name)
        s3.upload_file(
            Filename=str(path),
            Bucket=S3_BUCKET,
            Key=key,
            ExtraArgs=extra if extra else None,
            Callback=progress,
            Config=TRANSFER_CFG
        )
        print("\n[OK] 업로드 완료:", path.name)

        STATE["uploaded"][key_local] = digest
        save_state(STATE)

    except Exception as e:
        print("[ERR] 업로드 실패:", path.name, "-", e)
    finally:
        with LOCK:
            inflight.discard(path)

def upload_backlog():
    for p in sorted(LOCAL_DIR.glob("*")):
        if p.suffix.lower() in ALLOW_EXT:
            upload_to_s3(p)

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in ALLOW_EXT:
            threading.Thread(target=lambda: (time.sleep(1), upload_to_s3(p)), daemon=True).start()
    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in ALLOW_EXT:
            threading.Thread(target=lambda: (time.sleep(1), upload_to_s3(p)), daemon=True).start()

def main():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    print("[INFO] 감시 폴더:", LOCAL_DIR)
    print("[INFO] 대상 버킷/프리픽스:", f"s3://{S3_BUCKET}/{S3_PREFIX}")
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
