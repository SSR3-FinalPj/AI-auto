# generator_server_v3_s3only_mock.py
import os
import json
import uuid
import asyncio
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ===== 환경 변수 =====
WORKFLOW_JSON_PATH = Path(os.getenv("WORKFLOW_JSON_PATH", "./videotest3.json")).resolve()
BRIDGE_CALLBACK_URL= os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8000/api/video/callback")

class GenIn(BaseModel):
    request_id: str
    user_id: str
    img: Optional[str] = None
    english_text: Optional[str] = ""

# ---------- 유틸 ----------
def _patch_workflow(workflow: dict,
                    prompt_text: str,
                    img_value: Optional[str]) -> dict:
    wf = json.loads(json.dumps(workflow))  # deepcopy
    if "71" in wf and isinstance(wf["71"], dict):
        wf["71"].setdefault("inputs", {})
        wf["71"]["inputs"]["text"] = prompt_text or ""
    if img_value and "72" in wf and isinstance(wf["72"], dict):
        wf["72"].setdefault("inputs", {})
        wf["72"]["inputs"]["image"] = img_value
    return wf

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
        "message": "video generation completed (mock)",
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
app = FastAPI(title="Generator Server (mock, no ComfyUI)")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    if not payload.request_id:
        raise HTTPException(400, "request_id 누락")
    if not WORKFLOW_JSON_PATH.exists():
        raise HTTPException(500, f"워크플로 파일 없음: {WORKFLOW_JSON_PATH}")

    if not payload.img:
        await _callback_bridge_fail(payload.request_id, "img 누락")
        return JSONResponse({"ok": False, "error": "img missing"}, status_code=400)

    # 워크플로 로드/패치 (테스트용, 실제 실행은 안 함)
    wf = json.loads(WORKFLOW_JSON_PATH.read_text(encoding="utf-8"))
    _ = _patch_workflow(wf, payload.english_text or "", payload.img)

    # === ComfyUI 실행 부분 주석 처리 ===
    # try:
    #     prompt_id = await _submit_to_comfy(patched)
    # except Exception as e:
    #     await _callback_bridge_fail(payload.request_id, f"submit to comfy failed: {e}")
    #     return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    # async def _bg():
    #     try:
    #         filename, view_url = await _poll_history_for_mp4(prompt_id)
    #         await _callback_bridge_success(payload.request_id, payload.english_text or "", filename, view_url)
    #     except Exception as e:
    #         await _callback_bridge_fail(payload.request_id, str(e))
    # asyncio.create_task(_bg())
    # return JSONResponse({"ok": True, "prompt_id": prompt_id})
    # ====================================

    # 대신 가짜 결과를 만들어 콜백
    fake_prompt_id = uuid.uuid4().hex
    fake_filename = f"{payload.request_id}.mp4"
    fake_url = f"http://mockserver/output/{fake_filename}"

    async def _bg():
        await asyncio.sleep(2)
        await _callback_bridge_success(payload.request_id, payload.english_text or "", fake_filename, fake_url)

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "prompt_id": fake_prompt_id})
