# app.py (패치 완성본)
from fastapi import FastAPI, Body, HTTPException
from schemas import PromptRequest, PromptResponse, build_base_prompt_en#, ActPromptRequest
from schemas import VideoCallbackRequest, VideoCallbackResponse
from typing import List, Dict, Any
import httpx
import os

app = FastAPI(title="Bridge Server")

PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://localhost:8000")
VIDEO_SERVER_BASE = os.getenv("VIDEO_SERVER_BASE", "http://localhost:8002")

PROMPT_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts"
GENERATE_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts/generate"  # ← 실행 엔드포인트 프록시용 추가
VIDEO_ENDPOINT = f"{VIDEO_SERVER_BASE}/api/videos"


# 프롬프트 생성 요청 & 응답
@app.post("/api/generate-prompts", response_model=PromptResponse)
async def generate_prompts_from_env(payload: Dict[str, Any] = Body(...)):
    """
    1) dict 환경 데이터를 영어 프롬프트 문장으로 변환
    2) 프롬프트 서버 /api/prompts 호출
    3) 응답을 그대로 반환
    """
    base_prompt = build_base_prompt_en(payload)
    req = PromptRequest(base_prompt_en=base_prompt)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(PROMPT_ENDPOINT, json=req.model_dump())
            r.raise_for_status()
            data = r.json()
            return PromptResponse(**data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ★ 새로 추가: 프롬프트 생성 + ComfyUI 실행까지 한 번에
@app.post("/api/generate-video")   # response_model 생략: 프롬프트 서버 JSON 그대로 패스스루
async def generate_video_from_env(payload: Dict[str, Any] = Body(...)):
    """
    1) dict 환경 데이터를 영어 프롬프트로 변환
    2) 프롬프트 서버 /api/prompts/generate 호출(ComfyUI 워크플로 제출)
    3) 프롬프트 서버의 JSON( request_id, prompt_ids, history_urls )을 그대로 반환
    """
    base_prompt = build_base_prompt_en(payload)
    req = PromptRequest(base_prompt_en=base_prompt)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(GENERATE_ENDPOINT, json=req.model_dump())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 영상 생성 완료 콜백 전달
@app.post("/api/videos/callback", response_model=VideoCallbackResponse)
async def handle_callback(callback_data: VideoCallbackRequest):
    try:
        async with httpx.AsyncClient(timeout=100) as client:
            payload = callback_data.model_dump()
            r = await client.post(VIDEO_ENDPOINT, json=payload)
            r.raise_for_status()
            data = r.json()  # ← r.json()로 수정(괄호 필수)
            return VideoCallbackResponse(**data)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Java server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# if __name__ == "__main__":
#     import uvicorn, os
#     # 필요하면 기본 환경값도 세팅
#     os.environ.setdefault("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
#     os.environ.setdefault("JAVA_SERVER_BASE", "http://127.0.0.1:8080")
#     # reload 쓰려면 "모듈경로:앱변수" 문자열 형태로!
#     uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)