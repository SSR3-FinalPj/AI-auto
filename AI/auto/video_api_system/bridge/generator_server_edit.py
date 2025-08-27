# generator_server.py
import os
import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ===== 환경 변수 =====
COMFY_BASE_URL     = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188")
WORKFLOW_JSON_PATH = Path(os.getenv("WORKFLOW_JSON_PATH", "./videotest4.json")).resolve()
BRIDGE_CALLBACK_URL= os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8000/api/video/callback")
POLL_INTERVAL      = float(os.getenv("POLL_INTERVAL", "2.0"))
POLL_TIMEOUT       = int(os.getenv("POLL_TIMEOUT", "36000"))  # 초

# -------------------
# Models
# -------------------
class GenIn(BaseModel):
    requestId: str
    userId: str
    img: Optional[str] = None
    prompt_text: Optional[str] = ""   # prompt_text 그대로 유지

# -------------------
# Utils
# -------------------
async def _submit_to_comfy(patched_workflow: Dict[str, Any]) -> str:
    client_id = uuid.uuid4().hex
    payload = {"client_id": client_id, "prompt": patched_workflow}
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(f"{COMFY_BASE_URL}/prompt", json=payload)
        r.raise_for_status()
        data = r.json()
    pid = data.get("prompt_id") or data.get("promptId") or ""
    if not pid:
        raise RuntimeError("ComfyUI가 prompt_id를 반환하지 않았습니다.")
    return pid

async def _poll_history_for_mp4(prompt_id: str) -> Tuple[str, Optional[str]]:
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

            mp4s = []
            for _, v in hist.items():
                outputs = v.get("outputs") or {}
                for _, items in outputs.items():
                    if isinstance(items, list):
                        for it in items:
                            fn = it.get("filename")
                            if fn and fn.lower().endswith(".mp4"):
                                mp4s.append(fn)
            if mp4s:
                filename = mp4s[-1]
                q = urlencode({"filename": filename, "type": "output"})
                return filename, f"{COMFY_BASE_URL}/view?{q}"

            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("ComfyUI history polling timeout (no mp4)")
            await asyncio.sleep(POLL_INTERVAL)

async def _callback_bridge_success(requestId: str,
                                   userId: str,
                                   prompt_text: str,
                                   filename: Optional[str],
                                   comfy_view_url: Optional[str]) -> None:
    payload = {
        "eventId": f"evt_{requestId}_done",
        "userId": userId,
        "requestId": requestId,
        "prompt": prompt_text or "",
        "status": "SUCCESS",
        "message": "video generation completed",
        "createdAt": datetime.now().isoformat()
    }
    if filename:
        payload["videoKey"] = filename
    if comfy_view_url:
        payload["imageKey"] = comfy_view_url
    async with httpx.AsyncClient(timeout=30) as cli:
        await cli.post(BRIDGE_CALLBACK_URL, json=payload)

async def _callback_bridge_fail(requestId: str, userId: Optional[str], msg: str) -> None:
    payload = {
        "eventId": f"evt_{requestId}_failed",
        "userId": userId,
        "requestId": requestId,
        "status": "FAILED",
        "message": msg,
        "createdAt": datetime.now().isoformat()
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            await cli.post(BRIDGE_CALLBACK_URL, json=payload)
        except Exception:
            pass

# -------------------
# FastAPI
# -------------------
app = FastAPI(title="Generator Server for ComfyUI (videotest4, JSON pre-modify)")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    if not payload.requestId:
        raise HTTPException(400, "requestId 누락")
    if not WORKFLOW_JSON_PATH.exists():
        raise HTTPException(500, f"워크플로 파일 없음: {WORKFLOW_JSON_PATH}")

    if not payload.img:
        await _callback_bridge_fail(payload.requestId, payload.userId, "img 누락")
        return JSONResponse({"ok": False, "error": "img missing"}, status_code=400)

    wf = json.loads(WORKFLOW_JSON_PATH.read_text(encoding="utf-8"))

    # === 워크플로우 패치 부분만 수정 ===
    # LoadImageS3 노드(72)에 이미지 주입
    if "72" in wf and isinstance(wf["72"], dict):
        wf["72"].setdefault("widgets_values", [""])
        wf["72"]["widgets_values"][0] = payload.img

    # FramePackTimestampedTextEncode 노드(71)에 프롬프트 주입
    if "71" in wf and isinstance(wf["71"], dict):
        wf["71"].setdefault("widgets_values", ["", "", 1, 9, 0])
        wf["71"]["widgets_values"][0] = payload.prompt_text or wf["71"]["widgets_values"][0]

    WORKFLOW_JSON_PATH.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
    patched = json.loads(WORKFLOW_JSON_PATH.read_text(encoding="utf-8"))

    try:
        prompt_id = await _submit_to_comfy(patched)
    except Exception as e:
        await _callback_bridge_fail(payload.requestId, payload.userId, f"submit to comfy failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    async def _bg():
        try:
            filename, view_url = await _poll_history_for_mp4(prompt_id)
            await _callback_bridge_success(payload.requestId, payload.userId, payload.prompt_text or "", filename, view_url)
        except Exception as e:
            await _callback_bridge_fail(payload.requestId, payload.userId, str(e))

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "promptId": prompt_id})
