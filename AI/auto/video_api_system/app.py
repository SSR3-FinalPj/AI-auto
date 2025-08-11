# bridge_app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
import httpx
import os
import uuid

app = FastAPI(title="Bridge Server")

PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://localhost:8000")
PROMPT_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts"

#원본 데이터(기본값 0으로 세팅)
class EnvData(BaseModel):
    areaName: str = ""
    temperature: float = 0.0
    humidity: float = 0.0
    uvIndex: float = 0.0
    congestionLevel: str = ""
    maleRate: float = 0.0
    femaleRate: float = 0.0
    teenRate: float = 0.0
    twentyRate: float = 0.0
    thirtyRate: float = 0.0
    fourtyRate: float = 0.0
    fiftyRate: float = 0.0
    sixtyRate: float = 0.0
    seventyRate: float = 0.0

#Prompt Request&Response
class PromptRequest(BaseModel):
    base_prompt_en: str

class PromptResponse(BaseModel):
    request_id: str
    prompts: str

#기본 프롬프트
def build_base_prompt_en(env: EnvData) -> str:
    """
    환경 데이터를 영어 문장으로 변환
    """
    return (
        f"{env.areaName}, current temperature {env.temperature:.1f}°C, "
        f"humidity {env.humidity:.1f}%, UV index {env.uvIndex:.1f}, congestion level {env.congestionLevel}, "
        f"male ratio {env.maleRate:.1f}%, female ratio {env.femaleRate:.1f}%, "
        f"age distribution: teens {env.teenRate:.1f}%, twenties {env.twentyRate:.1f}%, "
        f"thirties {env.thirtyRate:.1f}%, forties {env.fourtyRate:.1f}%, fifties {env.fiftyRate:.1f}%, "
        f"sixties {env.sixtyRate:.1f}%, seventies {env.seventyRate:.1f}%"
    )

#프롬프트 생성
@app.post("/api/generate-prompts", response_model=PromptResponse)
async def generate_prompts_from_env(env_data: EnvData):
    """
    1. 자바에서 dict 형태로 받은 환경 데이터를 영어 프롬프트 문장으로 변환
    2. 프롬프트 서버 `/api/prompts` 호출
    3. 응답을 그대로 반환
    """
    # 1) base_prompt_en 생성
    base_prompt = build_base_prompt_en(env_data)

    # 2) 프롬프트 서버 요청 페이로드 구성
    payload = PromptRequest(
        base_prompt_en=base_prompt
    )

    # 3) 프롬프트 서버 호출
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PROMPT_ENDPOINT, json=payload.model_dump())
            r.raise_for_status()
            data = r.json()
            return PromptResponse(**data)

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# video_router = APIRouter(prefix="/api/videos", tags=["videos"])

# class VideoCallbackRequest(BaseModel):
#     promptId: str
#     efsPath: str
#     durationSec: int

# @video_router.post("/callback")
# async def handle_callback(request: VideoCallbackRequest):

#     logging.info(
#         f"Received video callback status: "
#         f"promptId={request.promptId}"
#         f"path={request.efsPath}"
#     )

# app = FastAPI(
#     title="Video Generation API",
#     description="API for creating prompts, generation videos, and handling callbacks",
#     version="0.2.0"
# )
# app.include_router(prompt_router)
# app.include_router(video_router)