import os
import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import httpx

from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ===== 환경 변수 =====
COMFY_BASE_URL      = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188")
WORKFLOW_YT_IMG     = Path(os.getenv("WORKFLOW_YT_IMG", "./youtube_image.json")).resolve()
WORKFLOW_YT_VIDEO   = Path(os.getenv("WORKFLOW_YT_VIDEO", "./youtube_video.json")).resolve()
BRIDGE_CALLBACK_URL = os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8001/api/video/callback")
POLL_INTERVAL       = float(os.getenv("POLL_INTERVAL", "2.0"))
POLL_TIMEOUT        = int(os.getenv("POLL_TIMEOUT", "36000"))

# -------------------
# Models
# -------------------
class GenIn(BaseModel):
    requestId: str
    jobId: str
    img: str                 # app.py에서 받은 이미지 → youtube_image.json의 LoadImageS3에 주입
    englishText: Optional[str] = ""
    platform: str            # youtube | reddit

# -------------------
# ComfyUI Utils
# -------------------
async def _submit_to_comfy(workflow: Dict[str, Any]) -> str:
    client_id = uuid.uuid4().hex
    payload = {"client_id": client_id, "prompt": workflow}
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(f"{COMFY_BASE_URL}/prompt", json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("prompt_id") or data.get("promptId") or ""

async def _poll_for_file(prompt_id: str, ext: str) -> str:
    """ComfyUI history에서 특정 확장자 파일이 생성될 때까지 polling"""
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
    async with httpx.AsyncClient(timeout=30) as cli:
        while True:
            r = await cli.get(f"{COMFY_BASE_URL}/history/{prompt_id}")
            if r.status_code == 404:
                await asyncio.sleep(POLL_INTERVAL)
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("history not found until timeout")
                continue
            r.raise_for_status()
            hist = r.json() or {}
            for _, v in hist.items():
                outputs = v.get("outputs") or {}
                for _, items in outputs.items():
                    if isinstance(items, list):
                        for it in items:
                            fn = it.get("filename")
                            if fn and fn.lower().endswith(ext):
                                return fn
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"ComfyUI polling timeout (no {ext})")
            await asyncio.sleep(POLL_INTERVAL)

# -------------------
# Callback
# -------------------
async def _callback_bridge(payload: GenIn,
                           status: str,
                           message: str,
                           resultKey: str = "") -> None:
    cb = {
        "eventId": f"evt_{payload.requestId}_{'done' if status == 'SUCCESS' else 'failed'}",
        "imageKey": payload.img,          # app.py로부터 받은 img 값 그대로
        "jobId": payload.jobId,
        "requestId": payload.requestId,
        "prompt": payload.englishText or "",
        "resultKey": resultKey,
        "status": status,
        "message": message,
        "type": payload.platform,
        "createdAt": datetime.now().isoformat()
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            await cli.post(BRIDGE_CALLBACK_URL, json=cb)
        except Exception:
            pass

# -------------------
# FastAPI
# -------------------
app = FastAPI(title="Generator Server for YouTube Workflows")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    if not payload.requestId:
        raise HTTPException(400, "requestId 누락")
    if payload.platform != "youtube":
        raise HTTPException(400, "현재는 youtube 플랫폼만 지원합니다.")

    if not WORKFLOW_YT_IMG.exists() or not WORKFLOW_YT_VIDEO.exists():
        raise HTTPException(500, "YouTube 워크플로 파일 없음")

    async def _yt_bg():
        try:
            # 1단계: 이미지 생성 워크플로 실행
            wf_img = json.loads(WORKFLOW_YT_IMG.read_text(encoding="utf-8"))
            # 프롬프트 주입 (노드 6: positive, 노드 7: negative)
            if "6" in wf_img and "inputs" in wf_img["6"]:
                wf_img["6"]["inputs"]["text"] = payload.englishText or ""
            if "7" in wf_img and "inputs" in wf_img["7"]:
                wf_img["7"]["inputs"]["text"] = ""
            # 이미지 입력 주입 (노드 16: LoadImageS3)
            if "16" in wf_img and "inputs" in wf_img["16"]:
                wf_img["16"]["inputs"]["image"] = payload.img

            pid_img = await _submit_to_comfy(wf_img)
            img_file = await _poll_for_file(pid_img, ".png")

            # 2단계: 비디오 생성 워크플로 실행
            wf_vid = json.loads(WORKFLOW_YT_VIDEO.read_text(encoding="utf-8"))
            # 프롬프트 주입 (노드 71: 텍스트 프롬프트)
            if "71" in wf_vid and "inputs" in wf_vid["71"]:
                wf_vid["71"]["inputs"]["text"] = payload.englishText or ""
            # 1단계 결과 이미지 입력 연결 (노드 81: LoadImageOutput)
            if "81" in wf_vid and "inputs" in wf_vid["81"]:
                wf_vid["81"]["inputs"]["image"] = img_file

            pid_vid = await _submit_to_comfy(wf_vid)
            video_file = await _poll_for_file(pid_vid, ".mp4")

            await _callback_bridge(payload, "SUCCESS", "youtube workflows completed", video_file)
        except Exception as e:
            await _callback_bridge(payload, "FAILED", str(e))

    asyncio.create_task(_yt_bg())
    return JSONResponse({"ok": True, "workflow": "youtube"})
