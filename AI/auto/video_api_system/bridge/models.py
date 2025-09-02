from pydantic import BaseModel
from typing import Optional, Any, Dict

# -------------------
# Models
# -------------------
class Weather(BaseModel):
    areaName: str
    temperature: str
    humidity: str
    uvIndex: str
    congestionLevel: str
    maleRate: str
    femaleRate: str
    teenRate: str
    twentyRate: str
    thirtyRate: str
    fortyRate: str
    fiftyRate: str
    sixtyRate: str
    seventyRate: str

class BridgeIn(BaseModel):
    img: str
    jobId: int
    platform: str
    isclient: bool = False
    weather: Weather
    user: Optional[Dict[str, Any]] = None

class Envelope(BaseModel):
    youtube: Optional[Dict[str, Any]] = None
    reddit: Optional[Dict[str, Any]]  = None
    topic: Optional[str] = None 
