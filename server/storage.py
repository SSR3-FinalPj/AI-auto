import os
from fastapi import UploadFile
from config import settings

CHUNK_SIZE = 1024 * 1024  # 1MB

async def save_streamed_file(file: UploadFile, media_type: str, media_id: int) -> str:
    dest_dir = os.path.join(settings.STORAGE_PATH, f"{media_type}s", str(media_id))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, file.filename)
    with open(dest_path, "wb") as out_file:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            out_file.write(chunk)
    return dest_path
