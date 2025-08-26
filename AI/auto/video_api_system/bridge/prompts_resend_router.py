from fastapi import APIRouter
from pydantic import BaseModel
from schemas import PromptCreateResponse
from prompt_store import load_prompt_record

router = APIRouter(prefix="/api/prompts", tags=["prompts-resend"])

class ResendRequest(BaseModel):
    request_id: str

@router.get("/{request_id}", response_model=PromptCreateResponse)
def get_prompts(request_id: str):
    rec = load_prompt_record(request_id)
    return PromptCreateResponse(request_id=rec.request_id, prompts=rec.prompts, negative=rec.negative)

@router.post("/resend", response_model=PromptCreateResponse)
def resend_prompts(req: ResendRequest):
    rec = load_prompt_record(req.request_id)
    return PromptCreateResponse(request_id=rec.request_id, prompts=rec.prompts, negative=rec.negative)
