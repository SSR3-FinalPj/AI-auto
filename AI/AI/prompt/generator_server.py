import os
import json
import uuid
import time
import shutil
import tempfile
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import httpx
import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, Body, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO

# =========================
# generator_server.py 설정 (ComfyUI)
# =========================
COMFY_BASE_URL       = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188")
WORKFLOW_YT_PATH     = Path(os.getenv("WORKFLOW_YT_PATH", "./youtube_video.json")).resolve()
WORKFLOW_REDDIT_PATH = Path(os.getenv("WORKFLOW_REDDIT_PATH", "./reddit_image.json")).resolve()
GEN_BRIDGE_CALLBACK  = os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8001/api/media/callback")
POLL_INTERVAL        = float(os.getenv("POLL_INTERVAL", "2.0"))
OUTPUT_DIR           = r"D:\ComfyUI\ComfyUI\output"

class GenInComfy(BaseModel):
    requestId: str
    jobId: int
    img: str
    englishText: Optional[str] = ""
    platform: str   # youtube | reddit
    isclient: Optional[bool] = False

async def _submit_to_comfy(patched_workflow: Dict[str, Any]) -> str:
    client_id = uuid.uuid4().hex
    payload = {"client_id": client_id, "prompt": patched_workflow}
    async with httpx.AsyncClient(timeout=300) as cli:
        r = await cli.post(f"{COMFY_BASE_URL}/prompt", json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("prompt_id") or data.get("promptId") or ""

async def _wait_for_history_and_get_output(prompt_id: str, ext: str, timeout: int, start_time: datetime) -> Optional[str]:
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=30) as cli:
        while asyncio.get_event_loop().time() < deadline:
            r = await cli.get(f"{COMFY_BASE_URL}/history/{prompt_id}")
            if r.status_code == 200:
                hist = r.json()
                for _, v in hist.items():
                    status_info = v.get("status", {})
                    if status_info.get("status_str") == "failed":
                        raise RuntimeError("ComfyUI execution failed")
                    if status_info.get("status_str") == "success" and status_info.get("completed"):
                        outputs = v.get("outputs", {})
                        for _, node_out in outputs.items():
                            if "images" in node_out:
                                for img in node_out["images"]:
                                    fn = img.get("filename")
                                    if fn and fn.lower().endswith(ext):
                                        full_path = os.path.join(OUTPUT_DIR, fn)
                                        if os.path.exists(full_path):
                                            mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
                                            if mtime > start_time:
                                                return fn
            await asyncio.sleep(POLL_INTERVAL)
    return None

async def _callback_bridge(payload: GenInComfy,
                           status: str,
                           message: str,
                           resultKey: str = "") -> None:
    mapped_type = (
        "video" if payload.platform == "youtube"
        else "image" if payload.platform == "reddit"
        else payload.platform
    )
    cb = {
        "eventId": f"evt_{payload.requestId}_{'done' if status == 'SUCCESS' else 'failed'}",
        "imageKey": payload.img,
        "jobId": payload.jobId,
        "requestId": payload.requestId,
        "prompt": payload.englishText or "",
        "resultKey": resultKey,
        "status": status,
        "message": message,
        "type": mapped_type,
        "createdAt": datetime.now().isoformat()
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            await cli.post(GEN_BRIDGE_CALLBACK, json=cb)
        except Exception as e:
            print(f"[ERROR] Callback 전송 실패: {e}")

async def _interrupt_comfy() -> bool:
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            r = await cli.post(f"{COMFY_BASE_URL}/interrupt")
            r.raise_for_status()
            data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
            return data.get("interrupted", False)
        except Exception as e:
            print(f"[ERROR] ComfyUI interrupt 실패: {e}")
            return False

# =========================
# veo3_server.py 설정 (Gemini+Veo3)
# =========================
load_dotenv()

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
VEO3_MODEL       = os.getenv("VEO3_MODEL", "")
ASPECT_RATIO     = os.getenv("VEO3_ASPECT_RATIO", "")
RESOLUTION       = os.getenv("VEO3_RESOLUTION", "")
PERSON_GEN       = os.getenv("VEO3_PERSON_GENERATION", "")
NEGATIVE_PROMPT  = os.getenv("VEO3_NEGATIVE_PROMPT", "")
VEO3_POLL_SEC    = int(os.getenv("VEO3_POLL_INTERVAL_S", "5"))

CALLBACK_URL     = os.getenv("BRIDGE_CALLBACK_URL", "")

S3_REGION        = os.getenv("S3_REGION", "")
S3_IMAGE_BUCKET  = os.getenv("S3_IMAGE_BUCKET", "")
S3_IMAGE_PREFIX  = os.getenv("S3_IMAGE_PREFIX", "")
S3_VIDEO_BUCKET  = os.getenv("S3_VIDEO_BUCKET", "")
S3_OUTPUT_PREFIX = os.getenv("S3_OUTPUT_PREFIX", "")
PRESIGN_EXPIRE_S = int(os.getenv("S3_PRESIGN_EXPIRE_S", "3600"))

LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "./output")
os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY 필요")
if not S3_IMAGE_BUCKET or not S3_VIDEO_BUCKET:
    raise RuntimeError("S3_IMAGE_BUCKET, S3_VIDEO_BUCKET 설정 필요")

s3_client = boto3.client("s3", region_name=S3_REGION, config=BotoConfig(signature_version="s3v4"))
s3_resource = boto3.resource("s3", region_name=S3_REGION)
client = genai.Client(api_key=GEMINI_API_KEY)

class GenInVeo(BaseModel):
    requestId: str
    jobId: int | str
    platform: Optional[str] = None
    img: str
    mascotImg: Optional[str] = None  # ← null/"" 허용: 합성 생략 분기
    isclient: Optional[bool] = None
    veoPrompt: str

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

def run_generation(job: GenInVeo):
    event_id = f"evt_{job.requestId}_{uuid.uuid4().hex[:6]}"
    prompt = job.veoPrompt
    try:
        print(f"[{now_iso()}] === 작업 시작 ===", flush=True)

        # 항상 베이스 이미지는 로드
        img_bytes, ctype1 = fetch_image_bytes_from_s3(job.img)

        # mascotImg 존재 여부로 합성 분기
        use_fused = bool(job.mascotImg and str(job.mascotImg).strip())
        if use_fused:
            print(f"[{now_iso()}] 합성 모드: 베이스+마스코트 이미지", flush=True)
            mascot_bytes, ctype2 = fetch_image_bytes_from_s3(job.mascotImg)  # 존재 가정
            print(f"[{now_iso()}] 이미지 2개 로드 완료", flush=True)

            img_part    = types.Part.from_bytes(data=img_bytes,    mime_type=ctype1)
            mascot_part = types.Part.from_bytes(data=mascot_bytes, mime_type=ctype2)

            nb_resp = client.models.generate_content(
                model="gemini-2.5-flash-image-preview",
                contents=[
                    "Create a new image by combining the mascot (second image) with the scene (first image). Seamless compositing.",
                    img_part,
                    mascot_part
                ],
            )
            merged_img_path = os.path.join(LOCAL_OUTPUT_DIR, f"{job.requestId}_merged.png")
            saved = False
            if nb_resp and nb_resp.candidates:
                for part in nb_resp.candidates[0].content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        image = Image.open(BytesIO(part.inline_data.data))
                        image.save(merged_img_path)
                        saved = True
                        break
            if not saved:
                raise RuntimeError("합성 이미지 없음")
            print(f"[{now_iso()}] 합성 이미지 생성 완료: {merged_img_path}", flush=True)

            with open(merged_img_path, "rb") as f:
                merged_bytes = f.read()
            merged_image_obj = types.Image(image_bytes=merged_bytes, mime_type="image/png")
        else:
            print(f"[{now_iso()}] 단일 이미지 모드: 합성 생략, 베이스 이미지로 바로 진행", flush=True)
            merged_image_obj = types.Image(image_bytes=img_bytes, mime_type=ctype1)

        operation = client.models.generate_videos(
            model=VEO3_MODEL,
            prompt=prompt,
            image=merged_image_obj,
            config=build_video_config(),
        )
        print(f"[{now_iso()}] Veo3 generate_videos 호출 성공. done={operation.done}", flush=True)

        while not operation.done:
            print(f"[{now_iso()}] 영상 생성 중... (polling)", flush=True)
            time.sleep(VEO3_POLL_SEC)
            operation = client.operations.get(operation)

        print(f"[{now_iso()}] 영상 생성 완료", flush=True)

        video = operation.response.generated_videos[0]
        client.files.download(file=video.video)
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fp:
            video.video.save(fp.name)
            tmp_path = fp.name

        local_name = f"{job.requestId}.mp4"
        local_path = os.path.join(LOCAL_OUTPUT_DIR, local_name)
        shutil.move(tmp_path, local_path)
        print(f"[{now_iso()}] 로컬 저장 완료: {local_path}", flush=True)

        out_key = f"{S3_OUTPUT_PREFIX.rstrip('/')}/{local_name}" if S3_OUTPUT_PREFIX else local_name
        _url = upload_video_to_s3(local_path, S3_VIDEO_BUCKET, out_key)
        print(f"[{now_iso()}] S3 업로드 완료: s3://{S3_VIDEO_BUCKET}/{out_key}", flush=True)

        cb = {
            "eventId": event_id,
            "requestId": job.requestId,
            "jobId": job.jobId,
            "prompt": prompt,
            "type": "video",
            "resultKey": local_name,
            "status": "SUCCESS",
            "message": "veo3 generation success"
                       + (" (with nanobanana fusion)" if use_fused else " (single image)"),
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
        except:
            pass

# =========================
# FastAPI 통합
# =========================
app = FastAPI(title="Unified Generator Server")
app.mount("/media", StaticFiles(directory=LOCAL_OUTPUT_DIR), name="media")

@app.post("/api/generate-media")
async def generate_comfy(payload: GenInComfy = Body(...)):
    if payload.isclient:
        interrupted = await _interrupt_comfy()
        if interrupted:
            await _callback_bridge(payload, "FAILED", "interrupted by client")
            await asyncio.sleep(2.0)

    if payload.platform == "youtube":
        wf_path = WORKFLOW_YT_PATH
        ext = ".mp4"
        poll_timeout = 3600
    elif payload.platform == "reddit":
        wf_path = WORKFLOW_REDDIT_PATH
        ext = ".png"
        poll_timeout = 300
    else:
        await _callback_bridge(payload, "FAILED", f"unsupported platform: {payload.platform}")
        return JSONResponse({"ok": False, "error": "unsupported platform"}, status_code=400)

    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    if payload.platform == "youtube":
        if "89" in wf: wf["89"]["inputs"]["image"] = payload.img
        if "95" in wf: wf["95"]["inputs"]["text"] = payload.englishText or ""
        if "96" in wf: wf["96"]["inputs"]["text"] = ""
        for _, node in wf.items():
            if isinstance(node, dict) and node.get("class_type") == "FramePack_TextEncode_Enhanced":
                node["inputs"]["text"] = payload.englishText or ""
    elif payload.platform == "reddit":
        if "16" in wf: wf["16"]["inputs"]["image"] = payload.img
        if "6" in wf: wf["6"]["inputs"]["text"] = payload.englishText or ""
        if "7" in wf: wf["7"]["inputs"]["text"] = ""

    start_time = datetime.now()
    try:
        prompt_id = await _submit_to_comfy(wf)
    except Exception as e:
        await _callback_bridge(payload, "FAILED", f"submit failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    async def _bg():
        try:
            result_key = await _wait_for_history_and_get_output(prompt_id, ext, poll_timeout, start_time)
            if result_key:
                await _callback_bridge(payload, "SUCCESS", f"{payload.platform} generation completed", result_key)
            else:
                await _callback_bridge(payload, "FAILED", f"no {ext} found within timeout")
        except Exception as e:
            await _callback_bridge(payload, "FAILED", str(e))
    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "promptId": prompt_id})

@app.post("/api/veo3-generate")
def veo3_generate(body: GenInVeo, bg: BackgroundTasks):
    if not body.veoPrompt or not body.requestId:
        raise HTTPException(status_code=400, detail="invalid payload")
    bg.add_task(run_generation, body)
    return {"accepted": True, "requestId": body.requestId, "model": VEO3_MODEL, "type": "veo3"}
