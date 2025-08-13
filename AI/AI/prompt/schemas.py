# D:\AI_auto\AI\AI\prompt\schemas.py
from typing import List, Optional
from pydantic import BaseModel, Field

class PromptRequest(BaseModel):
    base_prompt_en: str = Field(..., description="Base English prompt")
    stages: Optional[List[str]] = None
    same_camera_angle: bool = True
    consistent_framing: bool = True
    timelapse_hint: bool = True
    negative_override: Optional[str] = None

class PromptResponse(BaseModel):
    request_id: str
    prompts: List[str]
    negative: str
