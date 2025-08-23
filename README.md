# Prompt bridge api

### 1. app.py 위치
AI/auto/video_api_system/app.py

python version : 3.12.7

#### 2. 설치
pip install fastapi "uvicorn[standard]" httpx "pydantic>=2"

#### 3. 실행
py -m uvicorn app:app --port 8000 --reload


#### 4. api
/api/generate-prompts

requests_body:
```
{
  "areaName": "",
  "temperature": "",
  "humidity": "",
  "uvIndex": "",
  "congestionLevel": "",
  "maleRate": "",
  "femaleRate": "",
  "teenRate": "",
  "twentyRate": "",
  "thirtyRate": "",
  "fourtyRate": "",
  "fiftyRate": "",
  "sixtyRate": "",
  "seventyRate": ""
}
```

response:
```
{
  "request_id": "string",
  "prompts": "string"
}
```
