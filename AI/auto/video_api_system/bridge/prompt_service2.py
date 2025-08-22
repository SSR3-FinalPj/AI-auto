import os
import uuid
import json
import random
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Body  # ★ Request, Body 추가
import requests
from prompts_resend_router import router as prompts_resend_router
from prompt_store import save_prompt_record

# app.py와 스키마(계약) 일치: 중복 정의 제거
from schemas import (
    PromptRequest,
    PromptCreateResponse,
    PromptGenerateResponse,
    build_base_prompt_en,   # ★ 추가: 브리지 페이로드 → base_prompt_en 생성
)

# ===== 설정 =====
COMFYUI_BASE = os.getenv("COMFYUI_BASE", "http://127.0.0.1:8188")
COMFYUI_API_URL = f"{COMFYUI_BASE}/prompt"
BRIDGE_CALLBACK = os.getenv("BRIDGE_CALLBACK", "http://bridge:9000/api/video/callback")  # ★ 콜백 URL

# 추가(선택): S3 기본값
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
S3_BUCKET  = os.getenv("S3_BUCKET")                      # 기본 버킷(없으면 payload에서 받기)
S3_PREFIX  = os.getenv("S3_PREFIX", "comfyui/outputs")   # 키 프리픽스 규칙

# 환경변수 없으면 기본 경로(필요 시 환경변수로 덮어쓰기)
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH", r"D:\ComfyUI\workflows\testapi1.json")

# 워크플로 JSON의 노드 ID (문자열 키)
KSAMPLER_ID = os.getenv("KSAMPLER_ID", "3")
POS_TEXT_ID = os.getenv("POS_TEXT_ID", "6")
NEG_TEXT_ID = os.getenv("NEG_TEXT_ID", "7")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="Prompt Service")
app.include_router(prompts_resend_router)

# ===== 기본 타임랩스 단계 =====
DEFAULT_STAGES = ["morning", "afternoon", "golden hour", "twilight (light rain)", "rainy night"]

# ===== 로컬 응답 모델(미사용 가능) =====
from pydantic import BaseModel
class GenerateResponse(BaseModel):
    request_id: str
    prompt_ids: List[str]
    history_urls: List[str]

# ===== 유틸 =====
def _load_workflow() -> dict:
    if not os.path.exists(WORKFLOW_PATH):
        logging.error(f"Workflow file not found: {WORKFLOW_PATH}")
        raise HTTPException(status_code=500, detail="Workflow file not found")
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def build_prompt_line(base_en: str, stage: str, same_angle: bool, consistent: bool, timelapse: bool) -> str:
    tags = []
    if same_angle:
        tags.append("shot from the same camera angle")
    if consistent:
        tags.append("consistent framing")
    if timelapse:
        tags.append("timelapse sequence")
    tag_text = ", ".join(tags) if tags else ""
    return f"{base_en}, {stage}{', ' if tag_text else ''}{tag_text}"

def _submit_to_comfyui(positive: str, negative: str) -> str:
    wf = json.loads(json.dumps(_load_workflow()))  # deep copy
    if KSAMPLER_ID in wf and "inputs" in wf[KSAMPLER_ID]:
        wf[KSAMPLER_ID]["inputs"]["seed"] = random.randint(1, int(1e18))
    if POS_TEXT_ID in wf and "inputs" in wf[POS_TEXT_ID]:
        wf[POS_TEXT_ID]["inputs"]["text"] = positive
    if NEG_TEXT_ID in wf and "inputs" in wf[NEG_TEXT_ID]:
        wf[NEG_TEXT_ID]["inputs"]["text"] = negative

    client_id = str(uuid.uuid4())
    payload = {"prompt": wf, "client_id": client_id}
    logging.info(f"[SUBMIT] client_id={client_id}, pos_len={len(positive)}, neg_len={len(negative)}")

    try:
        r = requests.post(COMFYUI_API_URL, json=payload, timeout=(5, 120))
        logging.info(f"[COMFYUI] status={r.status_code}")
        logging.info(f"[COMFYUI] body={r.text[:400]}")
        r.raise_for_status()
    except requests.RequestException as e:
        logging.exception("[COMFYUI] HTTP error")
        raise HTTPException(status_code=502, detail=f"ComfyUI unreachable: {e}")

    try:
        data = r.json()
    except ValueError:
        logging.error(f"[COMFYUI] Invalid JSON response: {r.text[:400]}")
        raise HTTPException(status_code=502, detail="ComfyUI returned non-JSON response")

    pid = data.get("prompt_id")
    if not pid:
        logging.error(f"[COMFYUI] Missing prompt_id in response: {data}")
        raise HTTPException(status_code=502, detail="ComfyUI response missing prompt_id")
    return pid

def _pick(d: dict, keys) -> Optional[str]:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def _pick_request_id(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    rid = _pick(payload, ["request_id", "requestId"])
    if rid:
        return rid
    meta = payload.get("meta") or {}
    return _pick(meta, ["request_id", "requestId"])

# ===== 엔드포인트 =====
@app.post("/api/prompts", response_model=PromptCreateResponse)
def create_prompts(req: PromptRequest):
    stages = req.stages or DEFAULT_STAGES
    prompts = [
        build_prompt_line(req.base_prompt_en, stage, req.same_camera_angle, req.consistent_framing, req.timelapse_hint)
        for stage in stages
    ]
    negative = req.negative_override or "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"
    request_id = str(uuid.uuid4())
    save_prompt_record(request_id, prompts, negative)
    return PromptCreateResponse(request_id=request_id, prompts=prompts, negative=negative)

@app.post("/api/prompts/generate", response_model=PromptGenerateResponse)
async def generate_and_run(payload: dict = Body(...)):
    """
    브리지/직접 호출 모두 허용:
    - payload에 base_prompt_en이 있으면 그대로 사용
    - 없으면 build_base_prompt_en(payload)로 생성
    - 브리지에서 보낸 request_id가 있으면 그대로 사용(콜백/응답 공통)
    """
    # 0) request_id 결정
    request_id = _pick_request_id(payload) or str(uuid.uuid4())
    logging.info(f"[GENERATE] using request_id={request_id}")

    # 1) base_prompt_en 확보
    base_en = payload.get("base_prompt_en")
    if not base_en:
        try:
            base_en = build_base_prompt_en(payload)  # weather/youtube/reddit/user → 한 줄 영어 프롬프트
        except Exception as e:
            logging.exception("[BASE_PROMPT] build failed")
            raise HTTPException(400, f"missing base_prompt_en and failed to derive: {e}")

    # 2) 플래그/스테이지 결정
    stages = payload.get("stages") or DEFAULT_STAGES
    same_angle = payload.get("same_camera_angle", True)
    consistent = payload.get("consistent_framing", True)
    timelapse_hint = payload.get("timelapse_hint", True)
    negative = payload.get("negative_override") or "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"

    # (선택) 재전송 저장
    prompts = [
        build_prompt_line(base_en, stage, same_angle, consistent, timelapse_hint)
        for stage in stages
    ]
    try:
        save_prompt_record(request_id, prompts, negative)
    except Exception as e:
        logging.warning(f"[PROMPT_STORE] save skipped: {e}")

    # 3) ComfyUI 실행
    prompt_ids: List[str] = []
    history_urls: List[str] = []
    for p in prompts:
        pid = _submit_to_comfyui(p, negative)
        prompt_ids.append(pid)
        history_urls.append(f"{COMFYUI_BASE}/history/{pid}")

    # 4) 브리지 콜백 자동 발신
    
    s3_bucket = payload.get("s3_bucket") or S3_BUCKET
    s3_key    = payload.get("s3_key")
    if not s3_key and prompt_ids:
        s3_key = f"{S3_PREFIX.rstrip('/')}/{request_id}/{prompt_ids[0]}.mp4"
    video_url = payload.get("video_url")
    
    cb_payload = {
        "request_id": request_id,
        "event_id": f"evt_{prompt_ids[0] if prompt_ids else 'noid'}",
        "prompt_id": (prompt_ids[0] if prompt_ids else None),
        "video_id": (prompt_ids[0] if prompt_ids else None), # 일단은 prompt_id로 대체 나중에 None이나 진짜 video_id
        "prompt": (prompts[0] if prompts else None),
        "video_path": (history_urls[0] if history_urls else None),
        "video_s3_bucket": s3_bucket,
        "video_s3_key": s3_key,
        "video_url": video_url,
        "status": "SUCCESS",
        "message": None
    }
    try:
        logging.info(f"[CALLBACK] POST {BRIDGE_CALLBACK} json={json.dumps(cb_payload)[:300]}")
        requests.post(BRIDGE_CALLBACK, json=cb_payload, timeout=5)
    except Exception as e:
        logging.exception(f"[CALLBACK] failed: {e}")

    # 5) 응답
    return PromptGenerateResponse(
        request_id=request_id,
        prompt_ids=prompt_ids,
        history_urls=history_urls
    )
