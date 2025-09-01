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
WORKFLOW_JSON_PATH  = Path(os.getenv("WORKFLOW_JSON_PATH", "./youtube_video.json")).resolve()
BRIDGE_CALLBACK_URL = os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8001/api/video/callback")
POLL_INTERVAL       = float(os.getenv("POLL_INTERVAL", "2.0"))
POLL_TIMEOUT        = int(os.getenv("POLL_TIMEOUT", "36000"))  # 초

# -------------------
# Models
# -------------------
class GenIn(BaseModel):
    requestId: str
    jobId: str
    img: str
    englishText: Optional[str] = ""
    platform: str

# -------------------
# Utils
# -------------------
async def _submit_to_comfy(patched_workflow: Dict[str, Any]) -> str:
    client_id = uuid.uuid4().hex
    payload = {"client_id": client_id, "prompt": patched_workflow}
    async with httpx.AsyncClient(timeout=300) as cli:
        r = await cli.post(f"{COMFY_BASE_URL}/prompt", json=payload)
        r.raise_for_status()
        data = r.json()
    pid = data.get("prompt_id") or data.get("promptId") or ""
    if not pid:
        raise RuntimeError("ComfyUI가 prompt_id를 반환하지 않았습니다.")
    return pid

async def _poll_history_for_mp4(prompt_id: str) -> str:
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
                            if fn and fn.lower().endswith(".mp4"):
                                return fn
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("ComfyUI history polling timeout (no mp4)")
            await asyncio.sleep(POLL_INTERVAL)

async def _callback_bridge(payload: GenIn,
                           status: str,
                           message: str,
                           resultKey: str = "") -> None:
    cb = {
        "eventId": f"evt_{payload.requestId}_{'done' if status == 'SUCCESS' else 'failed'}",
        "imageKey": payload.img,
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
        except Exception as e:
            print(f"[ERROR] Callback 전송 실패: {e}")

# -------------------
# FastAPI
# -------------------
app = FastAPI(title="Generator Server for ComfyUI (youtube_video.json 전용)")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    if not payload.requestId:
        raise HTTPException(400, "requestId 누락")
    if not WORKFLOW_JSON_PATH.exists():
        raise HTTPException(500, f"워크플로 파일 없음: {WORKFLOW_JSON_PATH}")

    if not payload.img:
        await _callback_bridge(payload, "FAILED", "img 누락")
        return JSONResponse({"ok": False, "error": "img missing"}, status_code=400)

    # 워크플로 수정
    wf = json.loads(WORKFLOW_JSON_PATH.read_text(encoding="utf-8"))

    # 이미지 (노드 89)
    if "89" in wf and isinstance(wf["89"], dict):
        wf["89"].setdefault("inputs", {})
        wf["89"]["inputs"]["image"] = payload.img

    # 프롬프트 (노드 95)
    if "95" in wf and isinstance(wf["95"], dict):
        wf["95"].setdefault("inputs", {})
        wf["95"]["inputs"]["text"] = payload.englishText or ""

    # 네거티브 프롬프트 (노드 96)
    if "96" in wf and isinstance(wf["96"], dict):
        wf["96"].setdefault("inputs", {})
        wf["96"]["inputs"]["text"] = ""

    # FramePack Text Encode (Enhanced)
    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "FramePack_TextEncode_Enhanced":
            node.setdefault("inputs", {})
            node["inputs"]["text"] = payload.englishText or ""

    patched = wf

    # ComfyUI에 제출
    try:
        prompt_id = await _submit_to_comfy(patched)
    except Exception as e:
        await _callback_bridge(payload, "FAILED", f"submit to comfy failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    # 비동기 후처리
    async def _bg():
        try:
            filename = await _poll_history_for_mp4(prompt_id)
            await _callback_bridge(payload, "SUCCESS", "video generation completed", filename)
        except Exception as e:
            await _callback_bridge(payload, "FAILED", str(e))

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "promptId": prompt_id})
