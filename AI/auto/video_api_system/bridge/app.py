# app.py (패치 완성본)
import json
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from schemas import PromptRequest, build_base_prompt_en, delivery_report
from typing import Dict, Any
from confluent_kafka import Producer
from contextlib import asynccontextmanager
import httpx
import os

PROMPT_SERVER_BASE = os.getenv("PROMPT_SERVER_BASE", "http://localhost:8000")
#VIDEO_SERVER_BASE = os.getenv("VIDEO_SERVER_BASE", "http://localhost:8002")

#PROMPT_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts"
GENERATE_ENDPOINT = f"{PROMPT_SERVER_BASE}/api/prompts/generate"  # ← 실행 엔드포인트 프록시용 추가
#VIDEO_ENDPOINT = f"{VIDEO_SERVER_BASE}/api/videos"
# 브릿지 콜백 URL(프롬프트 생성 서버가 완료 콜백을 보낼 곳)
# 예: http://bridge:8000/api/videos/callback
BRIDGE_CALLBACK_URL = os.getenv("BRIDGE_CALLBACK_URL")
#Kafka bootstrap server
KAFKA_BOOTSTRAP_SERVERS = os.getenv(
    "KAFKA_BOOTSTRAP_SERVERS",
    ""
)
KAFKA_TOPIC_RESULTS = os.getenv("KAFKA_TOPIC_RESULTS", "new-topic")

producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup 시 할 일 있으면 여기에
    yield
    # shutdown 단계: 남은 메시지 밀어넣기
    try:
        producer.flush(5)
    except Exception as e:
        print("[Kafka] flush error:", e)

app = FastAPI(title="Bridge Server", lifespan=lifespan)


# ★ 새로 추가: 프롬프트 생성 + ComfyUI 실행까지 한 번에
@app.post("/api/generate-video")   # response_model 생략: 프롬프트 서버 JSON 그대로 패스스루
async def generate_video_from_env(payload: Dict[str, Any] = Body(...)):
    """
    1) dict 환경 데이터를 영어 프롬프트로 변환
    2) 프롬프트 서버 /api/prompts/generate 호출(ComfyUI 워크플로 제출)
    3) 프롬프트 서버의 JSON( request_id, prompt_ids, history_urls )을 그대로 반환
    """
    base_prompt = build_base_prompt_en(payload)
    req = PromptRequest(base_prompt_en=base_prompt).model_dump()

    if BRIDGE_CALLBACK_URL:
        req["callback_url"] = BRIDGE_CALLBACK_URL

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(GENERATE_ENDPOINT, json=req)
            r.raise_for_status()
        return JSONResponse(status_code=200, content={"status": "ok"})
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Prompt server unreachable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _pick_video_path(cb: Dict[str, Any]) -> str | None:
    cand = ["efsPath", "video_url", "file_url", "path", "storage_path", "output_url", "remote_url"]
    v = _pick(cb, cand)
    if v:
        return v
    outputs = cb.get("outputs") or {}
    return _pick(outputs, cand)

def _pick(cb: Dict[str, Any], keys):
    for k in keys:
        v = cb.get(k)
        if v is not None:
            return v
    return None

# 영상 생성 완료 콜백 전달
@app.post("/api/videos/callback")
async def handle_callback(callback_data: Dict[str, Any] = Body(...)):

    cb = callback_data
    video_id = _pick(cb)
    efsPath    = _pick_video_path(cb)
    prompt_id = _pick(cb)
    prompt     = _pick(cb)
    message    = _pick(cb, ["message", "status_message"]) or "Video generation completed"

    payload_to_java = {
        "video_id": video_id,
        "efsPath": efsPath,
        "prompt_id" : prompt_id,
        "prompt": prompt,
        "message": message,
    }

    try:
        key = efsPath or "no-key"
        producer.produce(
            topic=KAFKA_TOPIC_RESULTS,
            key=str(key),
            value=json.dumps(payload_to_java, ensure_ascii=False),
            callback=delivery_report
        )
        producer.poll(0)  # 콜백 처리 트리거 (non-blocking)

        # 콜백 호출자에게는 OK만
        return JSONResponse(status_code=200, content={"status": "ok"})
    except BufferError as e:
        # 내부 큐가 가득 찼을 때 등
        producer.poll(0.1)
        raise HTTPException(status_code=503, detail=f"Kafka buffer full: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# if __name__ == "__main__":
#     import uvicorn, os
#     # 필요하면 기본 환경값도 세팅
#     os.environ.setdefault("PROMPT_SERVER_BASE", "http://127.0.0.1:8000")
#     os.environ.setdefault("JAVA_SERVER_BASE", "http://127.0.0.1:8080")
#     # reload 쓰려면 "모듈경로:앱변수" 문자열 형태로!
#     uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)