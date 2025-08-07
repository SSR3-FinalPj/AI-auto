import requests
from pathlib import Path
import sys
import time
import mimetypes

URL = "http://localhost:8000/upload_media"
TOKEN = "your-secret-token"
VIDEO_DIR = Path(r"C:\Users\minne\Desktop\SK_FinalPJT\video_upload_system\data\videos")
IMG_DIR = Path(r"C:\Users\minne\Desktop\SK_FinalPJT\video_upload_system\data\images")
IMG_EXTENSIONS = {".jpg", ".png", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}

def upload_file(filepath:Path):

    content_type, _ = mimetypes.guess_type(filepath.name)
    if content_type is None:
        content_type = "application/octet-stream"

    with filepath.open("rb") as f:
        files = {"file": (filepath.name, f, content_type)}
        headers = {"Authorization": f"Bearer {TOKEN}"}
        resp = requests.post(URL, headers=headers, files=files)

        return resp.status_code, resp.json()
def main():

    file_paths = []

    if VIDEO_DIR.is_dir():
        file_paths.extend(
            [p for p in VIDEO_DIR.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS]
        )
    else:
        print(f"[ERROR] '{VIDEO_DIR}' 디렉터리가 없습니다.", file=sys.stderr)
    
    if IMG_DIR.is_dir():
        file_paths.extend(
            [p for p in IMG_DIR.iterdir() if p.suffix.lower() in IMG_EXTENSIONS]
        )
    else:
        print(f"[ERROR] '{IMG_DIR}' 디렉터리가 없습니다.", file=sys.stderr)

    if not file_paths:
        print("[INFO] 업로드할 파일을 찾을 수 없습니다.")
        return

    print(f"[INFO] 총 {len(file_paths)}개 파일 업로드를 시작합니다.")
    for idx, filepath in enumerate(file_paths, start=1):
        print(f"[{idx}/{len(file_paths)}] Uploading '{filepath.name}' ...")
        try:
            status, data = upload_file(filepath)
            if status ==200:
                print("완료 -> ", data)
            else:
                print(f"실패 (HTTP {status}) -> ", data)
        except Exception as e:
            print("예외 발생 ->", e)
        time.sleep(0.2)

if __name__ == "__main__":
    main()

