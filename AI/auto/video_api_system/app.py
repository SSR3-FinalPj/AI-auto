from fastapi import FastAPI, HTTPException, APIRouter
from pydantic import BaseModel
from uuid import uuid4
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

prompt_router = APIRouter(prefix="/api/prompts", tags=["prompts"])

class PromptRequest(BaseModel):
    data: dict
    templateId: str
#    parameters: dict = {}

class PromptResponse(BaseModel):
    promptId: str
    promptText: str

@prompt_router.post("", response_model=PromptResponse)
async def create_prompt(request: PromptRequest):
    #실제 템플릿 엔진 호출 로직
    prompt_id = str(uuid4())
    prompt_text = "프롬프트 내용. 템플릿 엔진 호출 로직 생성 후 호출 로직에서 받아올 예정"
    logging.info(f"Prompt created: id={prompt_id}")
    return PromptResponse(PromptId=prompt_id, promptText=prompt_text)

video_router = APIRouter(prefix="/api/videos", tags=["videos"])

class VideoCallbackRequest(BaseModel):
    promptId: str
    efsPath: str
    durationSec: int

@video_router.post("/callback")
async def handle_callback(request: VideoCallbackRequest):

    logging.info(
        f"Received video callback status: "
        f"promptId={request.promptId}"
        f"path={request.efsPath}"
    )

app = FastAPI(
    title="Video Generation API",
    description="API for creating prompts, generation videos, and handling callbacks",
    version="0.2.0"
)
app.include_router(prompt_router)
app.include_router(video_router)