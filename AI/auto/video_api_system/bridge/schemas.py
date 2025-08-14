from pydantic import BaseModel
from typing import List, Dict, Any

# #원본 날씨 데이터(기본값 ""으로 세팅)
# class EnvData(BaseModel):
#     areaName: str = ""
#     temperature: str = ""
#     humidity: str = ""
#     uvIndex: str = ""
#     congestionLevel: str = ""
#     maleRate: str = ""
#     femaleRate: str = ""
#     teenRate: str = ""
#     twentyRate: str = ""
#     thirtyRate: str = ""
#     fourtyRate: str = ""
#     fiftyRate: str = ""
#     sixtyRate: str = ""
#     seventyRate: str = ""

#Prompt Request&Response
class PromptRequest(BaseModel):
    base_prompt_en: Dict[str, Any]

# #반응 데이터 Request
# class ActPromptRequest(BaseModel):
#     act_base_prompt: Dict[str, str]

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]

#기본 프롬프트 생성(날씨데이터용)
def build_base_prompt_en(data) -> str:
    weather=""
    youtube=""
    reddit=""
    """
    데이터를 영어 문장으로 변환
    """
    if data["weather"]:
        weatenv = data["weather"]
        weather = (
        f"[weather]: {weatenv.areaname}, current temperature {weatenv.temperature}°C, "
        f"humidity {weatenv.humidity}%, UV index {weatenv.uvIndex}, congestion level {weatenv.congestionLevel}, "
        f"male ratio {weatenv.maleRate}%, female ratio {weatenv.femaleRate}%, "
        f"age distribution: teens {weatenv.teenRate}%, twenties {weatenv.twentyRate}%, "
        f"thirties {weatenv.thirtyRate}%, forties {weatenv.fourtyRate}%, fifties {weatenv.fiftyRate}%, "
        f"sixties {weatenv.sixtyRate}%, seventies {weatenv.seventyRate}%"
        )
    if data["youtube"]:
        youtenv = data["youtube"]
        youtube = (
        f"{youtenv}"
        )
    if data["reddit"]:
        reddenv = data["reddit"]
        reddit = (
        f"{reddenv}"
        )
    
    return weather + youtube + reddit
    

#영상 생성 완료 callback request&response
class VideoCallbackRequest(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int

class VideoCallbackResponse(BaseModel):
    request_id: str
    efsPath: str
    durationSec: int