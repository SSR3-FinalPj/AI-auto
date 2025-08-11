from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
import uuid

app = FastAPI()

# 시퀀스 단계 프리셋
DEFAULT_STAGES = ["morning", "afternoon", "golden hour", "twilight (light rain)", "rainy night"]

class PromptRequest(BaseModel):
    # 사용자가 영어로 주는 기본 프롬프트(장소/구도/피사체/스타일 등)
    base_prompt_en: str = Field(..., description="Base English prompt, e.g., 'Gyeongbokgung Palace, traditional Korean architecture'")
    # 단계(시간대/상황) 목록. 비우면 기본 타임랩스 단계로 생성
    stages: Optional[List[str]] = Field(default=None, description="e.g., ['morning','afternoon','golden hour','night rain']")
    # 일관성 옵션
    same_camera_angle: bool = True
    consistent_framing: bool = True
    timelapse_hint: bool = True   # 타임랩스라는 힌트를 덧붙일지
    # 네거티브 프롬프트(비우면 기본값 사용)
    negative_override: Optional[str] = None

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]
    negative: str

def build_prompt_line(
    base_en: str,
    stage: str,
    same_angle: bool,
    consistent: bool,
    timelapse: bool
) -> str:
    tags = []
    if same_angle:
        tags.append("shot from the same camera angle")
    if consistent:
        tags.append("consistent framing")
    if timelapse:
        tags.append("timelapse sequence")
    tag_text = ", ".join(tags) if tags else ""
    return f"{base_en}, {stage}{', ' if tag_text else ''}{tag_text}"

@app.post("/api/prompts", response_model=PromptResponse)
def create_prompts(req: PromptRequest):
    stages = req.stages or DEFAULT_STAGES

    prompts = [
        build_prompt_line(
            req.base_prompt_en,
            stage,
            same_angle=req.same_camera_angle,
            consistent=req.consistent_framing,
            timelapse=req.timelapse_hint
        )
        for stage in stages
    ]

    negative = req.negative_override or "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"

    return PromptResponse(
        request_id=str(uuid.uuid4()),
        prompts=prompts,
        negative=negative
    )
