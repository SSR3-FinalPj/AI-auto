# -*- coding: utf-8 -*-
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Tuple

import boto3
from boto3.s3.transfer import TransferConfig

# ===== 사용자 설정 (환경변수로도 오버라이드 가능) =====
S3_BUCKET   = os.getenv("S3_BUCKET", "your-bucket-name")
S3_PREFIX   = os.getenv("S3_PREFIX", "comfyui/outputs")  # 예: 'folderA/sub/' (슬래시 유무 무관)
LOCAL_DIR   = Path(os.getenv("LOCAL_DIR", r"D:\Downloads\s3-images"))
INCLUDE_EXT = tuple(ext.lower() for ext in os.getenv(
    "INCLUDE_EXT", ".png,.jpg,.jpeg,.webp,.gif"
).split(","))

# 전송(멀티파트) 설정: 큰 파일은 자동으로 분할 다운로드
MB = 1024 * 1024
TRANSFER_CFG = TransferConfig(
    multipart_threshold=16 * MB,   # 넘으면 멀티파트 전환
    multipart_chunksize=8 * MB,    # 파트 크기
    max_concurrency=8,             # 객체 1개당 내부 스레드 동시성
    use_threads=True
)

def iter_image_objects(s3, bucket: str, prefix: str) -> Iterable[Tuple[str, int]]:
    """
    이미지 확장자만 걸러 S3 키와 사이즈를 반환.
    list_objects_v2는 페이지당 최대 1000개라 paginator를 사용한다.
    """
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):            # 콘솔이 만든 '폴더' placeholder 건너뜀
                continue
            if key.lower().endswith(INCLUDE_EXT):
                yield key, int(obj["Size"])

def to_local_path(key: str, base_local: Path, prefix: str) -> Path:
    # prefix를 제거해 로컬 상대경로 구성
    rel = key[len(prefix):].lstrip("/") if prefix else key
    dest = base_local / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest

def need_download(dest: Path, remote_size: int) -> bool:
    # 멱등성: 같은 사이즈면 스킵 (ETag는 멀티파트 시 MD5가 아닐 수 있음)
    return not (dest.exists() and dest.stat().st_size == remote_size)

def download_one(s3, bucket: str, key: str, dest: Path, size: int):
    last_pct = -1
    def callback(bytes_amount):
        nonlocal last_pct
        if size > 0:
            pct = int(min(100, (dest.stat().st_size if dest.exists() else 0) * 100 / size))
            if pct != last_pct:
                print(f"\r[DOWN] {key} -> {dest}  {pct}%", end="")
                last_pct = pct

    s3.download_file(
        bucket, key, str(dest),
        Config=TRANSFER_CFG,
        Callback=callback  # 진행률 로그(간단)
    )
    print(f"\r[OK]   {key} -> {dest} ({size} bytes)")

def main():
    session = boto3.Session()  # 표준 자격증명/리전 탐색 규칙 사용
    s3 = session.client("s3")

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] bucket={S3_BUCKET} prefix={S3_PREFIX} -> local={LOCAL_DIR}")
    todo = []
    for key, size in iter_image_objects(s3, S3_BUCKET, S3_PREFIX):
        dest = to_local_path(key, LOCAL_DIR, S3_PREFIX)
        if need_download(dest, size):
            todo.append((key, dest, size))
        else:
            print(f"[SKIP] {dest} (same size)")

    if not todo:
        print("[INFO] 내려받을 이미지가 없습니다.")
        return

    # 여러 객체를 병렬로 다운로드(객체 단위 병렬; 각 객체 내부도 TransferConfig로 멀티파트 병렬)
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(download_one, s3, S3_BUCKET, k, d, sz) for (k, d, sz) in todo]
        for f in as_completed(futures):
            f.result()  # 예외 전파

if __name__ == "__main__":
    main()
