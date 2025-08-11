# bridge_app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
import httpx
import os
import uuid

app = FastAPI(title="Bridge Server")

PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://localhost:8001")
PROMPT_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts"

# ===== 자바에서 오는 원본 데이터 =====
class EnvData(BaseModel):
    areaName: str
    temperature: int
    humidity: int
    uvIndex: int
    congestionLevel: str
    maleRate: int
    femaleRate: int
    teenRate: int
    twentyRate: int
    thirtyRate: int
    fourtyRate: int
    fiftyRate: int
    sixtyRate: int
    seventyRate: int

# ===== 프롬프트 서버에 보낼 Request/Response =====
class PromptRequest(BaseModel):
    base_prompt_en: str

class PromptResponse(BaseModel):
    request_id: str
    prompts: str

def build_base_prompt_en(env: EnvData) -> str:
    """
    환경 데이터를 영어 문장으로 변환
    """
    return (
        f"{env.areaName}, current temperature {env.temperature}°C, "
        f"humidity {env.humidity}%, UV index {env.uvIndex}, congestion level {env.congestionLevel}, "
        f"male ratio {env.maleRate}%, female ratio {env.femaleRate}%, "
        f"age distribution: teens {env.teenRate}%, twenties {env.twentyRate}%, "
        f"thirties {env.thirtyRate}%, forties {env.fourtyRate}%, fifties {env.fiftyRate}%, "
        f"sixties {env.sixtyRate}%, seventies {env.seventyRate}%"
    )

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