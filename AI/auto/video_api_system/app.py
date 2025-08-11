# app.py  (Bridge Server: Spring -> Prompt Service)
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from pydantic.aliases import AliasChoices
from typing import Optional, List
import httpx
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="Bridge Server")

# 프롬프트 서버 실행형 엔드포인트 (/api/prompts/generate 권장, 별칭도 지원)
PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
PROMPT_GENERATE_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts/generate"

# ===== Spring에서 오는 EnvData =====
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
    # fourtyRate / fortyRate 모두 허용
    fourtyRate: int = Field(validation_alias=AliasChoices("fourtyRate", "fortyRate"))
    fiftyRate: int
    sixtyRate: int
    seventyRate: int

# ===== 프롬프트 서버 요청(Base prompt) =====
class PromptServerRequest(BaseModel):
    base_prompt_en: str
    stages: Optional[List[str]] = None
    same_camera_angle: bool = True
    consistent_framing: bool = True
    timelapse_hint: bool = True
    negative_override: Optional[str] = None

# ===== 프롬프트 서버 실행형 응답 =====
class PromptServerResponse(BaseModel):
    request_id: str
    prompt_ids: List[str]
    history_urls: List[str]

def build_base_prompt_en(env: EnvData) -> str:
    return (
        f"{env.areaName}, current temperature {env.temperature}°C, "
        f"humidity {env.humidity}%, UV index {env.uvIndex}, congestion level {env.congestionLevel}, "
        f"male ratio {env.maleRate}%, female ratio {env.femaleRate}%, "
        f"age distribution: teens {env.teenRate}%, twenties {env.twentyRate}%, "
        f"thirties {env.thirtyRate}%, forties {env.fourtyRate}%, fifties {env.fiftyRate}%, "
        f"sixties {env.sixtyRate}%, seventies {env.seventyRate}%"
    )

def guess_stages(env: EnvData) -> Optional[List[str]]:
    stages: List[str] = []
    if env.uvIndex >= 7:
        stages.append("bright afternoon")
    if env.congestionLevel.lower() in {"high", "very high"}:
        stages.append("evening rush")
    return stages or None

@app.post("/api/generate-prompts", response_model=PromptServerResponse)
async def generate_prompts_auto(req: Request):
    """
    - EnvData 형태가 오면 → base_prompt_en로 변환해서 프롬프트 서버 실행
    - 이미 PromptServerRequest(base_prompt_en) 형태가 오면 → 그대로 프롬프트 서버 실행
    """
    body = await req.json()
    logging.info(f"[BRIDGE] incoming keys: {list(body.keys())[:8]}...")

    # A) EnvData 파싱 시도
    try:
        env = EnvData(**body)
        payload_json = PromptServerRequest(
            base_prompt_en=build_base_prompt_en(env),
            stages=guess_stages(env),
            same_camera_angle=True,
            consistent_framing=True,
            timelapse_hint=True,
            negative_override=None
        ).model_dump()
        logging.info("[BRIDGE] parsed as EnvData → converted to PromptServerRequest")
    except Exception:
        # B) PromptServerRequest 파싱 시도
        try:
            payload_json = PromptServerRequest(**body).model_dump()
            logging.info("[BRIDGE] parsed as PromptServerRequest (passthrough)")
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Invalid body: expected EnvData or PromptServerRequest shape"
            )

    # 프롬프트 서버 실행형 엔드포인트 호출
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(PROMPT_GENERATE_ENDPOINT, json=payload_json)
            logging.info(f"[BRIDGE->PROMPT] status={r.status_code}")
            r.raise_for_status()
            data = r.json()
            return PromptServerResponse(**data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
