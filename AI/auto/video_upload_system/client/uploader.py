import argparse
import os
import httpx
from config import SERVER_URL, TOKEN

CHUNK_SIZE = 1024 * 1024  # 1MB

def stream_file(path):
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            yield chunk

def upload_media(file_path: str):
    filename = os.path.basename(file_path)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/octet-stream"
    }
    url = f"{SERVER_URL}/upload_media"
    # 스트리밍 POST
    with httpx.Client(timeout=None) as client:
        response = client.post(
            url,
            headers=headers,
            files={"file": (filename, stream_file(file_path), "application/octet-stream")}
        )
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream-upload a video file.")
    parser.add_argument("path", help="Path to local video file")
    args = parser.parse_args()

    result = upload_video(args.path)
    print(f"Upload succeeded! Video ID: {result['video_id']}, Status: {result['status']}")
