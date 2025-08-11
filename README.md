# Prompt bridge api

### app.py 위치
AI/auto/video_api_system/app.py

python version : 3.12.7

#### 설치
pip install fastapi "uvicorn[standard]" httpx "pydantic>=2"

#### 실행
py -m uvicorn app:app --port 8000 --reload


#### api
/api/generate-prompts

requests_body:
{
  "areaName": "",
  "temperature": 0,
  "humidity": 0,
  "uvIndex": 0,
  "congestionLevel": "",
  "maleRate": 0,
  "femaleRate": 0,
  "teenRate": 0,
  "twentyRate": 0,
  "thirtyRate": 0,
  "fourtyRate": 0,
  "fiftyRate": 0,
  "sixtyRate": 0,
  "seventyRate": 0
}

response:
{
  "request_id": "string",
  "prompts": "string"
}
