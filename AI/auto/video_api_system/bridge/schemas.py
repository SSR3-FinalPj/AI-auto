from pydantic import BaseModel
from typing import List, Dict

#원본 날씨 데이터(기본값 ""으로 세팅)
class EnvData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

#Prompt Request&Response
class PromptRequest(BaseModel):
    base_prompt_en: str

#반응 데이터 Request
class ActPromptRequest(BaseModel):
    act_base_prompt: Dict[str, str]

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]

#유튜브 반응데이터 
class YoutubeData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

#레딧 반응데이터 
class RedditData(BaseModel):
    areaName: str = ""
    temperature: str = ""
    humidity: str = ""
    uvIndex: str = ""
    congestionLevel: str = ""
    maleRate: str = ""
    femaleRate: str = ""
    teenRate: str = ""
    twentyRate: str = ""
    thirtyRate: str = ""
    fourtyRate: str = ""
    fiftyRate: str = ""
    sixtyRate: str = ""
    seventyRate: str = ""

#기본 프롬프트 생성(날씨데이터용)
def build_base_prompt_en(env: EnvData) -> str:
    """
    환경 데이터를 영어 문장으로 변환
    """
    return (
        f"{env.areaName}, current temperature {env.temperature}°C, "
        f"humidity {env.humidity}%, UV index {env.uvIndex}, congestion level {env.congestionLevel}, "
        f"male ratio {env.maleRate}%, female ratio {env.femaleRate}%, "
        f"age distribution: teens {env.teenRate}%, twenties {env.twentyRate}%, "
        f"thirties {env.thirtyRate}%, forties {env.fourtyRate}%, fifties {env.fiftyRate}%, "
        f"sixties {env.sixtyRate}%, seventies {env.seventyRate}%"
    )

#영상 생성 완료 callback request&response
class VideoCallbackRequest(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

class VideoCallbackResponse(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int