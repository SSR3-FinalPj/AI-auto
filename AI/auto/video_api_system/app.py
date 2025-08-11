from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import httpx
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="Bridge Server")

# ===== 외부 서버 기본 설정 =====
PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
VIDEO_SERVER_BASE  = os.getenv("VIDEO_SERVER_BASE",  "http://127.0.0.1:8002")

# 프롬프트 "실행형" 엔드포인트로 직접 호출 (ComfyUI까지 실행)
PROMPT_GENERATE_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts/generate"
VIDEO_ENDPOINT           = f"{VIDEO_SERVER_BASE}/api/videos"

# ===== Spring에서 오는 원본 데이터(문자열로 들어와도 OK) =====
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

# ===== 프롬프트 서버 "실행형" 응답 =====
class PromptGenerateResponse(BaseModel):
    request_id: str
    prompt_ids: List[str]
    history_urls: List[str]

# ===== 비디오 콜백 =====
class VideoCallbackRequest(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

class VideoCallbackResponse(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

# ===== 유틸 =====
def build_base_prompt_en(env: EnvData) -> str:
    return (
        f"{env.areaName}, current temperature {env.temperature}°C, "
        f"humidity {env.humidity}%, UV index {env.uvIndex}, congestion level {env.congestionLevel}, "
        f"male ratio {env.maleRate}%, female ratio {env.femaleRate}%, "
        f"age distribution: teens {env.teenRate}%, twenties {env.twentyRate}%, "
        f"thirties {env.thirtyRate}%, forties {env.fourtyRate}%, fifties {env.fiftyRate}%, "
        f"sixties {env.sixtyRate}%, seventies {env.seventyRate}%"
    )

# ===== 라우트 =====
@app.post("/api/generate-prompts", response_model=PromptGenerateResponse)
async def generate_prompts_from_env(env_data: EnvData):
    """
    1) EnvData → base_prompt_en 문자열로 변환
    2) 프롬프트 서버 실행형(/api/prompts/generate) 호출 → ComfyUI 큐 등록
    3) prompt_ids, history_urls 반환
    """
    payload = {"base_prompt_en": build_base_prompt_en(env_data)}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(PROMPT_GENERATE_ENDPOINT, json=payload)
            logging.info(f"[BRIDGE->PROMPT] status={r.status_code}")
            r.raise_for_status()
            data = r.json()
            return PromptGenerateResponse(**data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/videos/callback", response_model=VideoCallbackResponse)
async def handle_callback(callback_data: VideoCallbackRequest):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(VIDEO_ENDPOINT, json=callback_data.model_dump())
            r.raise_for_status()
            data = r.json()   # <- () 꼭 호출
            return VideoCallbackResponse(**data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Java server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # 로컬 개발 포트 8001 권장(8000은 프롬프트 서버가 사용)
    os.environ.setdefault("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=True)
