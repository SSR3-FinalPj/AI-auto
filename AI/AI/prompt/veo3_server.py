# veo3_server.py
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
from google.genai import types
from PIL import Image
from io import BytesIO

load_dotenv()

# Gemini / Veo3
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
VEO3_MODEL       = os.getenv("VEO3_MODEL", "veo-3.0-generate-001")
ASPECT_RATIO     = os.getenv("VEO3_ASPECT_RATIO", "")
RESOLUTION       = os.getenv("VEO3_RESOLUTION", "")
PERSON_GEN       = os.getenv("VEO3_PERSON_GENERATION", "allow_adult")
NEGATIVE_PROMPT  = os.getenv("VEO3_NEGATIVE_PROMPT", "")
POLL_INTERVAL_S  = int(os.getenv("VEO3_POLL_INTERVAL_S", "5"))

# Bridge 콜백
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

# boto3 클라이언트
s3_client = boto3.client(
    "s3",
    region_name=S3_REGION,
    config=BotoConfig(signature_version="s3v4"),
)
s3_resource = boto3.resource("s3", region_name=S3_REGION)

# Gemini API 클라이언트
client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------
# 요청 스키마 (app.py와 호환: img, mascotimg)
# -------------------
class GenIn(BaseModel):
    requestId: str
    jobId: int | str
    platform: Optional[str] = None
    img: str           # 첫 번째 이미지 (예: 배경)
    mascotImg: str     # 두 번째 이미지 (예: 마스코트)
    isclient: Optional[bool] = None
    veoPrompt: str     # 최종 영상 제작 프롬프트


app = FastAPI(title="Veo3 + NanoBanana Generator")
app.mount("/media", StaticFiles(directory=LOCAL_OUTPUT_DIR), name="media")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_video_params(aspect_ratio: str, resolution: str) -> Tuple[str, str]:
    ar = aspect_ratio.strip()
    res = resolution.strip().lower()
    # 공식 문서에 따르면 Veo 3의 9:16은 720p만 지원(1080p는 16:9 전용)
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


def fetch_image_bytes_from_s3(img: str) -> tuple[bytes, str]:
    bucket, key = parse_s3_uri_or_key(img)
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    ctype = obj.get("ContentType") or "image/jpeg"
    data = obj["Body"].read()
    return data, ctype


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
# 합성 + 영상 생성
# -------------------
def run_generation(job: GenIn):
    event_id = f"evt_{job.requestId}_{uuid.uuid4().hex[:6]}"
    prompt = job.veoPrompt

    try:
        print(f"[{now_iso()}] === 작업 시작 ===", flush=True)
        print(f"requestId={job.requestId}, jobId={job.jobId}", flush=True)

        # 1) S3에서 이미지 2개 로드
        img_bytes, ctype1 = fetch_image_bytes_from_s3(job.img)
        mascot_bytes, ctype2 = fetch_image_bytes_from_s3(job.mascotImg)
        print(f"[{now_iso()}] 이미지 2개 로드 완료", flush=True)

        # 2) 나노바나나(Gemini 2.5 Flash Image) API로 합성
        # Part.from_bytes는 키워드 인자 사용 필수 (data=..., mime_type=...)
        img_part    = types.Part.from_bytes(data=img_bytes,    mime_type=ctype1)
        mascot_part = types.Part.from_bytes(data=mascot_bytes, mime_type=ctype2)

        nb_prompt = (
            "Place the mascot from the second image clearly off to the side, positioned deep in a corner of the background from the first image. "
            "Do not render the mascot as a child. Make it look like a stylized mascot character, cartoon-like, not a real human. "
            "Photorealistic blending with the background scene, strong 3D feel, consistent lighting and color temperature, correct perspective, "
            "and grounded contact shadows. "
            "Scale the mascot to approximately adult human size so it fits the scene believably."
        )


        nb_resp = client.models.generate_content(
            model="gemini-2.5-flash-image-preview",
            contents=[nb_prompt, img_part, mascot_part],
        )

        # 2-1) 합성 이미지 저장
        merged_img_path = os.path.join(LOCAL_OUTPUT_DIR, f"{job.requestId}_merged.png")
        saved = False
        
        if nb_resp and nb_resp.candidates:
            candidate = nb_resp.candidates[0]
            if candidate and getattr(candidate, "content", None):
                for part in nb_resp.candidates[0].content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        image = Image.open(BytesIO(part.inline_data.data))
                        image.save(merged_img_path)
                        saved = True
                        break
                        
        if not saved:
            raise RuntimeError(f"합성 이미지가 응답에 없습니다. 응답 내용: {nb_resp}")
        print(f"[{now_iso()}] 합성 이미지 생성 완료: {merged_img_path}", flush=True)

        # 3) 합성 이미지를 Veo3 입력으로 사용 (Image-to-Video)
        with open(merged_img_path, "rb") as f:
            merged_bytes = f.read()
        merged_image_obj = types.Image(image_bytes=merged_bytes, mime_type="image/png")

        operation = client.models.generate_videos(
            model=VEO3_MODEL,
            prompt=prompt,
            image=merged_image_obj,
            config=build_video_config(),
        )
        print(f"[{now_iso()}] Veo3 generate_videos 호출 성공. done={operation.done}", flush=True)

        # 4) 완료까지 폴링
        while not operation.done:
            print(f"[{now_iso()}] 영상 생성 중... (polling)", flush=True)
            time.sleep(POLL_INTERVAL_S)
            operation = client.operations.get(operation)

        print(f"[{now_iso()}] 영상 생성 완료", flush=True)

        # 5) 비디오 다운로드
        resp = getattr(operation, "response", None)
        if not resp or not getattr(resp, "generated_videos", None):
            raise RuntimeError(f"Veo3 응답에 generated_videos가 없습니다. 전체 응답: {operation}")

        videos = resp.generated_videos
        if len(videos) == 0 or videos[0] is None:
            raise RuntimeError(f"Veo3 응답에 유효한 video 객체가 없습니다. 전체 응답: {operation}")

        video = videos[0]
        client.files.download(file=video.video)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fp:
            video.video.save(fp.name)
            tmp_path = fp.name

        local_name = f"{job.requestId}.mp4"
        local_path = os.path.join(LOCAL_OUTPUT_DIR, local_name)
        shutil.move(tmp_path, local_path)
        print(f"[{now_iso()}] 로컬 저장 완료: {local_path}", flush=True)


        # 6) S3 업로드
        out_key = f"{S3_OUTPUT_PREFIX.rstrip('/')}/{local_name}" if S3_OUTPUT_PREFIX else local_name
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
            "message": "veo3 generation success (with nanobanana fusion)",
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
            "message": f"generation failed: {e}",
            "createdAt": now_iso(),
        }
        try:
            post_callback(cb)
        except Exception as e2:
            print(f"콜백 실패: {e2}", flush=True)


# 엔드포인트
@app.post("/api/veo3-generate")
def veo3_generate(body: GenIn, bg: BackgroundTasks):
    if not body.veoPrompt or not body.requestId:
        raise HTTPException(status_code=400, detail="invalid payload")
    bg.add_task(run_generation, body)
    return {"accepted": True, "requestId": body.requestId, "model": VEO3_MODEL, "type": "veo3"}
