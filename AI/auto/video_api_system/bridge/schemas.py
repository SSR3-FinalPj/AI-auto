# schemas.py (권장 정본)
from typing import List, Optional, Dict, Any
from pydantic import AnyHttpUrl, BaseModel, Field

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


# 콜백
class VideoCallbackRequest(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int
    prompt : str

class VideoCallbackResponse(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int
    prompt : str

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