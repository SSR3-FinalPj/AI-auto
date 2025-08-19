import os
import time
import threading
from typing import Dict, List
from pydantic import BaseModel
from fastapi import HTTPException

PROMPT_TTL_SEC = int(os.getenv("PROMPT_TTL_SEC", "86400"))  # 24h 기본
_STORE_LOCK = threading.Lock()
_PROMPT_STORE: Dict[str, "PromptRecord"] = {}

class PromptRecord(BaseModel):
    request_id: str
    prompts: List[str]
    negative: str
    created_at: float  # epoch seconds

def save_prompt_record(request_id: str, prompts: List[str], negative: str) -> None:
    rec = PromptRecord(
        request_id=request_id,
        prompts=prompts,
        negative=negative,
        created_at=time.time(),
    )
    with _STORE_LOCK:
        _PROMPT_STORE[request_id] = rec

def _purge_expired(now: float) -> None:
    with _STORE_LOCK:
        expired = [k for k, v in _PROMPT_STORE.items() if now - v.created_at > PROMPT_TTL_SEC]
        for k in expired:
            _PROMPT_STORE.pop(k, None)

def load_prompt_record(request_id: str) -> PromptRecord:
    now = time.time()
    _purge_expired(now)
    with _STORE_LOCK:
        rec = _PROMPT_STORE.get(request_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"request_id not found or expired: {request_id}")
    return rec
