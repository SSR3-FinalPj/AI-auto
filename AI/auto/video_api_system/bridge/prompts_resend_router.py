from fastapi import APIRouter
from pydantic import BaseModel
from schemas import PromptResponse  # 기존 schemas 재사용
from prompt_store import load_prompt_record

router = APIRouter(prefix="/api/prompts", tags=["prompts-resend"])

class ResendRequest(BaseModel):
    request_id: str

@router.get("/{request_id}", response_model=PromptResponse)
def get_prompts(request_id: str):
    rec = load_prompt_record(request_id)
    return PromptResponse(request_id=rec.request_id, prompts=rec.prompts, negative=rec.negative)

@router.post("/resend", response_model=PromptResponse)
def resend_prompts(req: ResendRequest):
    rec = load_prompt_record(req.request_id)
    return PromptResponse(request_id=rec.request_id, prompts=rec.prompts, negative=rec.negative)
