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
COMFY_BASE_URL       = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188")
WORKFLOW_YT_PATH     = Path(os.getenv("WORKFLOW_YT_PATH", "./youtube_video.json")).resolve()
WORKFLOW_REDDIT_PATH = Path(os.getenv("WORKFLOW_REDDIT_PATH", "./reddit_image.json")).resolve()
BRIDGE_CALLBACK_URL  = os.getenv("BRIDGE_CALLBACK_URL", "http://127.0.0.1:8001/api/video/callback")
POLL_INTERVAL        = float(os.getenv("POLL_INTERVAL", "2.0"))

# ===== 로컬 output 폴더 =====
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/output")

# -------------------
# Models
# -------------------
class GenIn(BaseModel):
    requestId: str
    jobId: int
    img: str
    englishText: Optional[str] = ""
    platform: str   # youtube | reddit
    isclient: Optional[bool] = False  

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

async def _wait_for_history_and_get_output(prompt_id: str, ext: str, timeout: int, start_time: datetime) -> Optional[str]:
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=30) as cli:
        while asyncio.get_event_loop().time() < deadline:
            r = await cli.get(f"{COMFY_BASE_URL}/history/{prompt_id}")
            if r.status_code == 200:
                hist = r.json()
                for _, v in hist.items():
                    status_info = v.get("status", {})
                    if status_info.get("status_str") == "failed":
                        raise RuntimeError("ComfyUI execution failed")
                    if status_info.get("status_str") == "success" and status_info.get("completed"):
                        outputs = v.get("outputs", {})
                        for _, node_out in outputs.items():
                            if "images" in node_out:
                                for img in node_out["images"]:
                                    fn = img.get("filename")
                                    if fn and fn.lower().endswith(ext):
                                        full_path = os.path.join(OUTPUT_DIR, fn)
                                        if os.path.exists(full_path):
                                            mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
                                            if mtime > start_time:
                                                print(f"[DEBUG] New file detected: {full_path}")
                                                return fn
            await asyncio.sleep(POLL_INTERVAL)
    return None

async def _callback_bridge(payload: GenIn,
                           status: str,
                           message: str,
                           resultKey: str = "") -> None:
    
    mapped_type = (
        "video" if payload.platform == "youtube"
        else "image" if payload.platform == "reddit"
        else payload.platform
    )
    
    cb = {
        "eventId": f"evt_{payload.requestId}_{'done' if status == 'SUCCESS' else 'failed'}",
        "imageKey": payload.img,
        "jobId": payload.jobId,
        "requestId": payload.requestId,
        "prompt": payload.englishText or "",
        "resultKey": resultKey,
        "status": status,
        "message": message,
        "type": mapped_type,
        "createdAt": datetime.now().isoformat()
    }
    print(f"[DEBUG] 콜백 전송 준비: {cb}")
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            r = await cli.post(BRIDGE_CALLBACK_URL, json=cb)
            print("[DEBUG] 콜백 응답:", r.status_code, r.text)
        except Exception as e:
            print(f"[ERROR] Callback 전송 실패: {e}")

async def _interrupt_comfy():
    async with httpx.AsyncClient(timeout=30) as cli:
        try:
            r = await cli.post(f"{COMFY_BASE_URL}/interrupt")
            print("[DEBUG] interrupt response:", r.status_code, r.text)
            r.raise_for_status()
            print("[INFO] 실행중인 ComfyUI 워크플로우 중단됨")
        except Exception as e:
            print(f"[ERROR] ComfyUI interrupt 실패: {e}")

# -------------------
# FastAPI
# -------------------
app = FastAPI(title="Generator Server (Debugging)")

@app.post("/generate")
async def generate(payload: GenIn = Body(...)):
    print("[DEBUG] 요청 수신:", payload.dict())

    if payload.isclient:
        print("[DEBUG] isclient=True, interrupt 호출 시도")
        await _interrupt_comfy()
        await _callback_bridge(payload, "FAILED", "interrupted by client")
        await asyncio.sleep(5.0)
    else:
        print("[DEBUG] isclient=False, 그냥 실행")

    if payload.platform == "youtube":
        wf_path = WORKFLOW_YT_PATH
        ext = ".mp4"
        poll_timeout = 3600
    elif payload.platform == "reddit":
        wf_path = WORKFLOW_REDDIT_PATH
        ext = ".png"
        poll_timeout = 300
    else:
        await _callback_bridge(payload, "FAILED", f"unsupported platform: {payload.platform}")
        return JSONResponse({"ok": False, "error": "unsupported platform"}, status_code=400)

    if not wf_path.exists():
        raise HTTPException(500, f"워크플로 파일 없음: {wf_path}")

    if not payload.img:
        await _callback_bridge(payload, "FAILED", "img 누락")
        return JSONResponse({"ok": False, "error": "img missing"}, status_code=400)

    wf = json.loads(wf_path.read_text(encoding="utf-8"))

    if payload.platform == "youtube":
        if "89" in wf:
            wf["89"].setdefault("inputs", {})
            wf["89"]["inputs"]["image"] = payload.img
        if "95" in wf:
            wf["95"].setdefault("inputs", {})
            wf["95"]["inputs"]["text"] = payload.englishText or ""
        if "96" in wf:
            wf["96"].setdefault("inputs", {})
            wf["96"]["inputs"]["text"] = ""
        for _, node in wf.items():
            if isinstance(node, dict) and node.get("class_type") == "FramePack_TextEncode_Enhanced":
                node.setdefault("inputs", {})
                node["inputs"]["text"] = payload.englishText or ""

    elif payload.platform == "reddit":
        if "16" in wf:
            wf["16"].setdefault("inputs", {})
            wf["16"]["inputs"]["image"] = payload.img
        if "6" in wf:
            wf["6"].setdefault("inputs", {})
            wf["6"]["inputs"]["text"] = payload.englishText or ""
        if "7" in wf:
            wf["7"].setdefault("inputs", {})
            wf["7"]["inputs"]["text"] = ""

    patched = wf
    start_time = datetime.now()

    try:
        prompt_id = await _submit_to_comfy(patched)
        print("[DEBUG] 새 워크플로우 제출, prompt_id:", prompt_id)
    except Exception as e:
        await _callback_bridge(payload, "FAILED", f"submit to comfy failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    async def _bg():
        try:
            result_key = await _wait_for_history_and_get_output(prompt_id, ext, poll_timeout, start_time)
            if result_key:
                await _callback_bridge(payload, "SUCCESS", f"{payload.platform} generation completed", result_key)
            else:
                await _callback_bridge(payload, "FAILED", f"no {ext} found in history/output within timeout")
        except Exception as e:
            await _callback_bridge(payload, "FAILED", str(e))

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "promptId": prompt_id})
