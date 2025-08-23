# generator_server_v3_s3only.py
import os
import json
import uuid
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ===== 환경 변수 =====
COMFY_BASE_URL     = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188")
WORKFLOW_JSON_PATH = Path(os.getenv("WORKFLOW_JSON_PATH", "./videotest3.json")).resolve()
BRIDGE_CALLBACK_URL= os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8000/api/video/callback")
POLL_INTERVAL      = float(os.getenv("POLL_INTERVAL", "2.0"))
POLL_TIMEOUT       = int(os.getenv("POLL_TIMEOUT", "36000"))  # 초

class GenIn(BaseModel):
    request_id: str
    user_id: str
    img: Optional[str] = None          # app.py에서 넘어오는 값 → 그대로 72.image에 매핑
    english_text: Optional[str] = ""   # app.py에서 넘어오는 영어 프롬프트

# ---------- 유틸 ----------
def _patch_workflow(workflow: Dict[str, Any],
                    prompt_text: str,
                    img_value: Optional[str]) -> Dict[str, Any]:
    """
    - 71(FramePackTimestampedTextEncode).inputs.text ← prompt_text
    - 72(LoadImageS3).inputs.image ← img_value (브리지에서 받은 값 그대로)
    """
    wf = json.loads(json.dumps(workflow))  # deepcopy

    # 71.text
    if "71" in wf and isinstance(wf["71"], dict):
        wf["71"].setdefault("inputs", {})
        wf["71"]["inputs"]["text"] = prompt_text or ""

    # 72.image
    if img_value and "72" in wf and isinstance(wf["72"], dict):
        wf["72"].setdefault("inputs", {})
        wf["72"]["inputs"]["image"] = img_value

    return wf

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
    """
    /history/{prompt_id}에서 .mp4 파일명을 찾아 반환.
    """
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

async def _callback_bridge_success(request_id: str,
                                   prompt_text: str,
                                   filename: Optional[str],
                                   comfy_view_url: Optional[str]) -> None:
    event_id = f"evt_{request_id}_done"
    payload = {
        "request_id": request_id,
        "event_id": event_id,
        "prompt": prompt_text or "",
        "status": "SUCCESS",
        "message": "video generation completed",
    }
    if filename:
        payload["video_path"] = filename
    if comfy_view_url:
        payload["video_url"] = comfy_view_url
    async with httpx.AsyncClient(timeout=30) as cli:
        await cli.post(BRIDGE_CALLBACK_URL, json=payload)

async def _callback_bridge_fail(request_id: str, msg: str) -> None:
    event_id = f"evt_{request_id}_failed"
    payload = {
        "request_id": request_id,
        "event_id": event_id,
        "status": "FAILED",
        "message": msg,
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            await cli.post(BRIDGE_CALLBACK_URL, json=payload)
        except Exception:
            pass

# ---------- FastAPI ----------
app = FastAPI(title="Generator Server for ComfyUI (videotest3, direct S3 key)")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    if not payload.request_id:
        raise HTTPException(400, "request_id 누락")
    if not WORKFLOW_JSON_PATH.exists():
        raise HTTPException(500, f"워크플로 파일 없음: {WORKFLOW_JSON_PATH}")

    if not payload.img:
        await _callback_bridge_fail(payload.request_id, "img 누락")
        return JSONResponse({"ok": False, "error": "img missing"}, status_code=400)

    # 워크플로 로드/패치
    wf = json.loads(WORKFLOW_JSON_PATH.read_text(encoding="utf-8"))
    patched = _patch_workflow(wf, payload.english_text or "", payload.img)

    try:
        prompt_id = await _submit_to_comfy(patched)
    except Exception as e:
        await _callback_bridge_fail(payload.request_id, f"submit to comfy failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    async def _bg():
        try:
            filename, view_url = await _poll_history_for_mp4(prompt_id)
            await _callback_bridge_success(payload.request_id, payload.english_text or "", filename, view_url)
        except Exception as e:
            await _callback_bridge_fail(payload.request_id, str(e))

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "prompt_id": prompt_id})
