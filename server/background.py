import subprocess
import os
import logging
from models import SessionLocal, MediaMeta
from config import settings

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_video(video_id: int):
    """
    비디오 트랜스코딩, 썸네일 생성 등을 수행.
    예시: ffmpeg를 호출해 MP4를 HLS로 변환하고, 썸네일 추출.
    HLS 데이터를 저장.
    """
    db = SessionLocal()
    video = db.query(MediaMeta).get(video_id)
    if not video:
        logger.error(f"Video with id {video_id} not found.")
        db.close()
        return
    folder = "videos"
    video_dir = os.path.join(settings.STORAGE_PATH, folder, str(video_id))
    input_path = os.path.join(video_dir, video.filename)
    output_dir = os.path.join(video_dir, "hls")
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        "ffmpeg", "-i", input_path,
        "-codec:", "copy",
        "-start_number", "0",
        "-hls_time", "10",
        "-hls_list_size", "0",
        "-f", "hls",
        os.path.join(output_dir, "index.m3u8")
    ]

    try:
        logger.info(f"Processing video {video_id}: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info(f"ffmpeg stdout: {result.stdout}")
        logger.info(f"ffmpeg stderr: {result.stderr}")
        video.status = "processed"
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to process video {video_id}.")
        logger.error(f"ffmpeg stderr: {e.stderr}")
        video.status = "failed"
    except Exception as e:
        logger.error(f"An unexpected error occurred during video processing: {e}")
        video.status = "failed"
    finally:
        db.commit()
        db.close()
