import os
import time
import uuid
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx
import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from google import genai
from google.genai import types  # GenerateVideosConfig, Image 등

load_dotenv()

# Gemini / Veo3
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
VEO3_MODEL       = os.getenv("VEO3_MODEL", "veo-3.0-generate-001")
ASPECT_RATIO     = os.getenv("VEO3_ASPECT_RATIO", "16:9")
RESOLUTION       = os.getenv("VEO3_RESOLUTION", "1080p")
PERSON_GEN       = os.getenv("VEO3_PERSON_GENERATION", "allow_adult")
NEGATIVE_PROMPT  = os.getenv("VEO3_NEGATIVE_PROMPT", "")
POLL_INTERVAL_S  = int(os.getenv("VEO3_POLL_INTERVAL_S", "5"))

# Bridge 콜백
#CALLBACK_URL     = os.getenv("BRIDGE_CALLBACK_URL", "http://localhost:8000/api/video/callback")
CALLBACK_URL     = os.getenv("BRIDGE_CALLBACK_URL", "http://localhost:8001/api/video/callback")

# S3 in/out
S3_REGION        = os.getenv("S3_REGION", "")
S3_IMAGE_BUCKET  = os.getenv("S3_IMAGE_BUCKET", "")
S3_IMAGE_PREFIX  = os.getenv("S3_IMAGE_PREFIX", "")
S3_VIDEO_BUCKET  = os.getenv("S3_VIDEO_BUCKET", "")
S3_OUTPUT_PREFIX = os.getenv("S3_OUTPUT_PREFIX", "")
PRESIGN_EXPIRE_S = int(os.getenv("S3_PRESIGN_EXPIRE_S", "86400"))

# 로컬 저장 경로
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "")
os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

# 필수값 확인
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY 가 필요합니다(.env 설정 확인).")
if not S3_IMAGE_BUCKET or not S3_VIDEO_BUCKET:
    raise RuntimeError("S3_IMAGE_BUCKET, S3_VIDEO_BUCKET 을 .env에 설정하세요.")

# boto3 클라이언트/리소스
s3_client = boto3.client(
    "s3",
    region_name=S3_REGION,
    config=BotoConfig(signature_version="s3v4"),
)
s3_resource = boto3.resource("s3", region_name=S3_REGION)

# Gemini API 클라이언트
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------
# 요청 스키마
# -------------------
class GenIn(BaseModel):
    requestId: str
    jobId: int | str
    platform: Optional[str] = None
    img: Optional[str] = None
    isclient: Optional[bool] = None
    veoPrompt: str   # 프롬프트는 veoPrompt 필수

app = FastAPI(title="Veo3 Generator (Image→Video, S3 & Local)")
app.mount("/media", StaticFiles(directory=LOCAL_OUTPUT_DIR), name="media")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def normalize_video_params(aspect_ratio: str, resolution: str) -> Tuple[str, str]:
    ar = aspect_ratio.strip()
    res = resolution.strip().lower()
    if ar == "9:16" and res == "1080p":
        res = "720p"
    return ar, res

def build_video_config() -> types.GenerateVideosConfig:
    ar, res = normalize_video_params(ASPECT_RATIO, RESOLUTION)
    return types.GenerateVideosConfig(
        aspect_ratio=ar,
        resolution=res,
        person_generation=PERSON_GEN or None,
        negative_prompt=NEGATIVE_PROMPT or None,
    )

def _join_key(prefix: str, name: str) -> str:
    p = (prefix or "").strip().strip("/")
    n = name.strip().lstrip("/")
    return f"{p}/{n}" if p else n

def parse_s3_uri_or_key(img: str) -> tuple[str, str]:
    if not img:
        raise ValueError("img 가 비어있습니다.")
    if img.startswith("s3://"):
        _, rest = img.split("s3://", 1)
        bucket, key = rest.split("/", 1)
        return bucket, key
    if "/" not in img:
        key = _join_key(S3_IMAGE_PREFIX, img)
        return S3_IMAGE_BUCKET, key
    key = img.lstrip("/")
    return S3_IMAGE_BUCKET, key

def fetch_image_from_s3(img: str) -> types.Image:
    bucket, key = parse_s3_uri_or_key(img)
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    ctype = obj.get("ContentType") or "image/jpeg"
    data = obj["Body"].read()
    return types.Image(image_bytes=data, mime_type=ctype)

def upload_video_to_s3(local_path: str, dest_bucket: str, dest_key: str) -> str:
    s3_resource.Bucket(dest_bucket).upload_file(
        local_path, dest_key, ExtraArgs={"ContentType": "video/mp4"}
    )
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": dest_bucket, "Key": dest_key},
        ExpiresIn=PRESIGN_EXPIRE_S,
    )
    return url

def post_callback(payload: dict):
    with httpx.Client(timeout=15) as cli:
        cli.post(CALLBACK_URL, json=payload)

# -------------------
# 생성 작업
# -------------------
def run_generation(job: GenIn):
    event_id = f"evt_{job.requestId}_{uuid.uuid4().hex[:6]}"
    prompt = job.veoPrompt

    try:
        print(f"[{now_iso()}] === 영상 생성 요청 시작 ===", flush=True)
        print(f"requestId={job.requestId}, jobId={job.jobId}, prompt={prompt}", flush=True)
        print(f"img={job.img}", flush=True)

        # 1) S3에서 이미지 로드
        image_obj = fetch_image_from_s3(job.img or "")
        print(f"[{now_iso()}] 이미지 로드 완료 (S3)", flush=True)

        # 2) Veo 3 API 호출
        operation = client.models.generate_videos(
            model=VEO3_MODEL,
            prompt=prompt,
            image=image_obj,
            config=build_video_config(),
        )
        print(f"[{now_iso()}] generate_videos 호출 성공", flush=True)
        print(f"operation 초기 상태: done={operation.done}", flush=True)

        # 3) 완료까지 폴링
        while not operation.done:
            print(f"[{now_iso()}] 영상 생성 중... (polling)", flush=True)
            time.sleep(POLL_INTERVAL_S)
            operation = client.operations.get(operation)

        print(f"[{now_iso()}] 영상 생성 완료", flush=True)

        # 4) 비디오 다운로드
        video = operation.response.generated_videos[0]
        client.files.download(file=video.video)
        print(f"[{now_iso()}] 비디오 다운로드 완료", flush=True)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fp:
            video.video.save(fp.name)
            tmp_path = fp.name
        print(f"[{now_iso()}] 임시 파일 저장: {tmp_path}", flush=True)

        # 5) 임시 → 로컬 이동
        local_name = f"{job.requestId}.mp4"
        local_path = os.path.join(LOCAL_OUTPUT_DIR, local_name)
        shutil.move(tmp_path, local_path)
        print(f"[{now_iso()}] 로컬 저장 완료: {local_path}", flush=True)

        # 6) S3 업로드
        out_key = f"{S3_OUTPUT_PREFIX.rstrip('/')}/{job.requestId}.mp4" if S3_OUTPUT_PREFIX else f"{job.requestId}.mp4"
        _presigned_url = upload_video_to_s3(local_path, S3_VIDEO_BUCKET, out_key)
        print(f"[{now_iso()}] S3 업로드 완료: s3://{S3_VIDEO_BUCKET}/{out_key}", flush=True)

        # 7) 콜백
        cb = {
            "eventId": event_id,
            "requestId": job.requestId,
            "jobId": job.jobId,
            "prompt": prompt,
            "type": "video",
            "resultKey": local_name,
            "status": "SUCCESS",
            "message": "veo3 generation success",
            "createdAt": now_iso(),
        }
        post_callback(cb)
        print(f"[{now_iso()}] 콜백 전송 완료", flush=True)

    except Exception as e:
        print(f"[{now_iso()}] 오류 발생: {e}", flush=True)
        cb = {
            "eventId": event_id,
            "requestId": job.requestId,
            "jobId": job.jobId,
            "prompt": prompt,
            "type": "video",
            "resultKey": "nothing",
            "status": "FAILED",
            "message": f"veo3 generation failed: {e}",
            "createdAt": now_iso(),
        }
        try:
            post_callback(cb)
            print(f"[{now_iso()}] 실패 콜백 전송 완료", flush=True)
        except Exception as e2:
            print(f"[{now_iso()}] 실패 콜백 전송 실패: {e2}", flush=True)

# 엔드포인트
@app.post("/api/veo3-generate")
def veo3_generate(body: GenIn, bg: BackgroundTasks):
    if not body.veoPrompt or not body.requestId:
        raise HTTPException(status_code=400, detail="invalid payload")

    bg.add_task(run_generation, body)
    return {"accepted": True, "requestId": body.requestId, "model": VEO3_MODEL, "type": "veo3"}
