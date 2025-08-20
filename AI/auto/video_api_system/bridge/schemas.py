# schemas.py (권장 정본)
from typing import List, Optional, Dict, Any
from pydantic import AnyHttpUrl, BaseModel, Field
from urllib.parse import urlparse
from pathlib import PurePosixPath
import uuid


# 프롬프트 서버에 보낼 요청 스키마 (문자열 기반)
class PromptRequest(BaseModel):
    base_prompt_en: str = Field(..., description="Base English prompt")
    stages: Optional[List[str]] = None
    same_camera_angle: bool = True
    consistent_framing: bool = True
    timelapse_hint: bool = True
    negative_override: Optional[str] = None

class PromptResponse(BaseModel):
    request_id: str
    prompt_ids: List[str]
    history_urls: List[str]

# (선택) 자바에서 넘어온 원시 데이터 → 영어 문장 변환기
def build_base_prompt_en(data: Dict[str, Any]) -> str:
    weather = ""
    youtube = ""
    reddit = ""
    user = ""

    w = data.get("weather")
    if w:
        # 딕셔너리 키 접근 + 케이스 일치
        # areaName / temperature / humidity ... 키를 실제 자바 JSON에 맞춰 조정
        area = w.get("areaName", "")
        temp = w.get("temperature", "")
        hum  = w.get("humidity", "")
        uvi  = w.get("uvIndex", "")
        cong = w.get("congestionLevel", "")
        male = w.get("maleRate", "")
        female = w.get("femaleRate", "")
        teen = w.get("teenRate", "")
        twenty = w.get("twentyRate", "")
        thirty = w.get("thirtyRate", "")
        fourty = w.get("fourtyRate", "")
        fifty = w.get("fiftyRate", "")
        sixty = w.get("sixtyRate", "")
        seventy = w.get("seventyRate", "")
        weather = (
            f"[weather]: {area}, current temperature {temp}°C, "
            f"humidity {hum}%, UV index {uvi}, congestion level {cong}, "
            f"male ratio {male}%, female ratio {female}%, "
            f"age distribution: teens {teen}%, twenties {twenty}%, "
            f"thirties {thirty}%, forties {fourty}%, fifties {fifty}%, "
            f"sixties {sixty}%, seventies {seventy}%"
        )

    y = data.get("youtube")
    if y:
        youtube = f" [youtube]: {y}"

    r = data.get("reddit")
    if r:
        reddit = f" [reddit]: {r}"
    
    u = data.get("user")
    if u:
        user = f" [user]: {u}"

    return (weather + youtube + reddit + user).strip()


def _pick(cb: Dict[str, Any], keys):
    for k in keys:
        v = cb.get(k)
        if v is not None:
            return v
    return None

def delivery_report(err, msg):
    if err is not None:
        print(f"Delivery failed: {err}")
    else:
        print(f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}")
        
def _pick(d: Dict[str, Any], keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None

def _pick_request_id(cb: Dict[str, Any]) -> str | None:
    rid = _pick(cb, ["request_id", "requestId"])
    if rid: return rid
    meta = cb.get("meta") or {}
    return _pick(meta, ["request_id", "requestId"])

def _pick_video_path(cb: Dict[str, Any]) -> str | None:
    cand = ["efsPath", "video_url", "file_url", "path", "storage_path", "output_url", "remote_url"]
    v = _pick(cb, cand)
    if v: return v
    outputs = cb.get("outputs") or {}
    return _pick(outputs, cand)

def _derive_video_id_from_path(p: str | None) -> str | None:
    if not p: return None
    try:
        path = urlparse(p).path or p
    except Exception:
        path = p
    name = PurePosixPath(path).name
    if name in ("index.m3u8", "index.mp4", "index"):
        name = PurePosixPath(path).parent.name
    if "." in name:
        name = name.split(".", 1)[0]
    return name or None

def _pick_video_id(cb: Dict[str, Any], video_path: str | None) -> str | None:
    return _pick(cb, ["video_id", "videoId", "file_id"]) or _derive_video_id_from_path(video_path)

def _pick_prompt_text(cb: Dict[str, Any]) -> str | None:
    return _pick(cb, ["prompt", "final_prompt", "prompt_text", "used_prompt"])

def _pick_prompt_id(cb: Dict[str, Any]) -> str | None:
    pid = _pick(cb, ["prompt_id", "promptId"])
    if pid: return pid
    pids = cb.get("prompt_ids") or cb.get("promptIds")
    if isinstance(pids, list) and pids:
        return str(pids[0])
    meta = cb.get("meta") or {}
    pid = _pick(meta, ["prompt_id", "promptId"])
    return pid or _pick_request_id(cb)  # 최후 폴백

def _pick_event_id(cb: Dict[str, Any]) -> str:
    return _pick(cb, ["event_id", "eventId", "id"]) or uuid.uuid4().hex  # 없으면 생성


# class PromptResponse(BaseModel):
#     request_id: str
#     prompt_ids: List[str]
#     history_urls: List[str]
