# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
import httpx
import os
import uuid

app = FastAPI(title="Bridge Server")

PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://localhost:8000")
VIDEO_SERVER_BASE = os.getenv("VIDEO_SERVER_BASE", "http://localhost:8002")
PROMPT_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts"
VIDEO_ENDPOINT = f"{VIDEO_SERVER_BASE}/api/videos"

#원본 데이터(기본값 0으로 세팅)
class EnvData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

#Prompt Request&Response
class PromptRequest(BaseModel):
    base_prompt_en: str

class ActPromptRequest(BaseModel):
    act_base_prompt: List[str]

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]

#기본 프롬프트
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

class YoutubeData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

@app.post("/api/youtube-prompts")
async def youtube_prompts_from_env(env_data: YoutubeData):

    payload = ActPromptRequest(
        act_base_prompt = env_data.model_dump()
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PROMPT_ENDPOINT, json=payload.model_dump())
            r.raise_for_status()
        return {
            "status": "success",
            "message": "Youtube prompt request forwarded to prompt server"
        }

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RedditData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

@app.post("/api/reddit-prompts")
async def reddit_prompts_from_env(env_data: RedditData):

    payload = ActPromptRequest(
        act_base_prompt = env_data.model_dump()
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PROMPT_ENDPOINT, json=payload.model_dump())
            r.raise_for_status()
        return {
            "status": "success",
            "message": "Reddit prompt request forwarded to prompt server"
        }

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




class VideoCallbackRequest(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

class VideoCallbackResponse(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

@app.post("/api/videos/callback")
async def handle_callback(callback_data : VideoCallbackRequest):
    try:
        async with httpx.AsyncClient(timeout=100) as client:
            payload = callback_data.model_dump()
            r = await client.post(VIDEO_ENDPOINT, json=payload)
            r.raise_for_status()
            data = r.json
            return VideoCallbackResponse(**data)

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Java server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn, os
    # 필요하면 기본 환경값도 세팅
    os.environ.setdefault("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
    os.environ.setdefault("JAVA_SERVER_BASE", "http://127.0.0.1:8080")
    # reload 쓰려면 "모듈경로:앱변수" 문자열 형태로!
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)