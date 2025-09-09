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
    user: Optional[str] = None

class Envelope(BaseModel):
    topic: Dict[str, Any] = None 


class VeoBridge(BaseModel):
    img: str
    jobId: int
    platform: str
    isclient: bool = False
    weather: Optional[Weather]
    beforeprompt: Optional[str] = None 
    user: Optional[str] = None
    element: Optional[Dict[str, Any]]

