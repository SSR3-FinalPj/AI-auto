import os
import uuid
import json
import random
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests

# ===== 설정 =====
COMFYUI_BASE = os.getenv("COMFYUI_BASE", "http://127.0.0.1:8188")
COMFYUI_API_URL = f"{COMFYUI_BASE}/prompt"

# 환경변수 없으면 기본 경로로 (원하면 반드시 환경변수로 덮어쓰기)
WORKFLOW_PATH = os.getenv("WORKFLOW_PATH", r"D:\ComfyUI\workflows\testapi1.json")

# 네 워크플로 JSON과 동일 (KSampler=3, positive=6, negative=7)
KSAMPLER_ID = os.getenv("KSAMPLER_ID", "3")
POS_TEXT_ID = os.getenv("POS_TEXT_ID", "6")
NEG_TEXT_ID = os.getenv("NEG_TEXT_ID", "7")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="Prompt Service with ComfyUI")

# ===== 기본 타임랩스 단계 =====
DEFAULT_STAGES = ["morning", "afternoon", "golden hour", "twilight (light rain)", "rainy night"]

# ===== 요청/응답 모델 =====
class PromptRequest(BaseModel):
    base_prompt_en: str = Field(..., description="Base English prompt, e.g., 'Gyeongbokgung Palace, traditional Korean architecture'")
    stages: Optional[List[str]] = None
    same_camera_angle: bool = True
    consistent_framing: bool = True
    timelapse_hint: bool = True
    negative_override: Optional[str] = None

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]
    negative: str

class GenerateRequest(PromptRequest):
    pass

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

    # 랜덤 시드
    if KSAMPLER_ID in wf and "inputs" in wf[KSAMPLER_ID]:
        wf[KSAMPLER_ID]["inputs"]["seed"] = random.randint(1, int(1e18))
    # 텍스트 주입
    if POS_TEXT_ID in wf and "inputs" in wf[POS_TEXT_ID]:
        wf[POS_TEXT_ID]["inputs"]["text"] = positive
    if NEG_TEXT_ID in wf and "inputs" in wf[NEG_TEXT_ID]:
        wf[NEG_TEXT_ID]["inputs"]["text"] = negative

    client_id = str(uuid.uuid4())
    payload = {"prompt": wf, "client_id": client_id}

    logging.info(f"[SUBMIT] client_id={client_id}, pos_len={len(positive)}, neg_len={len(negative)}")

    try:
        r = requests.post(COMFYUI_API_URL, json=payload, timeout=60)
        logging.info(f"[COMFYUI] status={r.status_code}")
        logging.info(f"[COMFYUI] body={r.text[:400]}")
        r.raise_for_status()
    except requests.RequestException as e:
        logging.exception("[COMFYUI] HTTP error")
        raise HTTPException(status_code=502, detail=str(e))

    data = r.json()
    pid = data.get("prompt_id")
    if not pid:
        logging.error(f"[COMFYUI] Missing prompt_id in response: {data}")
        raise HTTPException(status_code=502, detail="ComfyUI response missing prompt_id")
    return pid

# ===== 엔드포인트 =====
@app.post("/api/prompts", response_model=PromptResponse)
def create_prompts(req: PromptRequest):
    stages = req.stages or DEFAULT_STAGES
    prompts = [
        build_prompt_line(req.base_prompt_en, stage, req.same_camera_angle, req.consistent_framing, req.timelapse_hint)
        for stage in stages
    ]
    negative = req.negative_override or "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"
    return PromptResponse(request_id=str(uuid.uuid4()), prompts=prompts, negative=negative)

# 실행형 엔드포인트(별칭 2개 모두 제공)
@app.post("/api/prompts/generate", response_model=GenerateResponse)
@app.post("/api/generate-prompts", response_model=GenerateResponse)
def generate_and_run(req: GenerateRequest):
    stages = req.stages or DEFAULT_STAGES
    prompts = [
        build_prompt_line(req.base_prompt_en, stage, req.same_camera_angle, req.consistent_framing, req.timelapse_hint)
        for stage in stages
    ]
    negative = req.negative_override or "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"

    prompt_ids: List[str] = []
    history_urls: List[str] = []

    for p in prompts:
        pid = _submit_to_comfyui(p, negative)
        prompt_ids.append(pid)
        history_urls.append(f"{COMFYUI_BASE}/history/{pid}")

    return GenerateResponse(
        request_id=str(uuid.uuid4()),
        prompt_ids=prompt_ids,
        history_urls=history_urls
    )
