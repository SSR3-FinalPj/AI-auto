# AI-Auto

## 폴더 구조
```
AI-auto/
├── AI/
│   ├── AI/
│   │   ├── prompt/                 # 영상생성 관련 서버/워크플로우
│   │   │   ├── generator_server.py
│   │   │   ├── veo3_server.py
│   │   │   ├── reddit_image.json   # 워크플로우 JSON
│   │   │   ├── youtube_video.json  # 워크플로우 JSON
│   │   │   ├── Dockerfile
│   │   │   └── requirements.txt
│   │   └── docker-compose.yml
│   └── auto/
│       └── video_api_system/       # 브리지 서버 API 시스템
│           ├── docker-compose.yml
│           └── bridge/
│               ├── app.py
│               ├── llm_client.py
│               ├── models.py
│               ├── push_image.sh
│               ├── Dockerfile
│               └── requirements.txt
├── .gitignore
└── README.md
```

## 프로젝트 개요
AI-Auto는 AI 모델과 자동화 파이프라인을 활용해 이미지/영상 생성 및 처리 작업을 자동화하는 리포지토리 입니다.
ComfyUI 워크플로우(JSON)와 LLM API를 연동하여, Reddit/YouTube 등 다양한 입력 기반의 멀티모달 콘텐츠를 자동으로 생성/제어할 수 있습니다.

## 주요 기능
### 멀티모달 프롬프트 서버
generator_server.py, veo3_server.py 기반 서버
워크플로우(JSON: reddit_image.json, youtube_video.json) 실행

### 영상 API 시스템
bridge/app.py를 통한 영상 생성 요청/처리
llm_client.py로 LLM 연동
push_image.sh 스크립트로 이미지 업로드 자동화

### Docker 기반 배포
docker-compose.yml로 프론트/백엔드 및 브릿지 서버 관리

### 확장성 높은 구조
영상생성 모듈과 브릿지 모듈을 분리 → 독립 실행 가능
새로운 워크플로우 JSON 추가 시 손쉽게 확장 가능

## 기술 스택
언어/환경: Python 3.12+
AI/ML: OpenAI / Groq LLM API, ComfyUI 워크플로우
인프라: Docker, Docker Compose
자동화 스크립트: Shell (push_image.sh)

#### 설치
pip install fastapi "uvicorn[standard]" httpx "pydantic>=2"

#### 실행
py -m uvicorn app:app --port 8000 --reload
py -m uvicorn generator_server:app --port 9001 --reload (comfyui)
py -m uvicorn veo3_server:app --port 9001 --reload (veo3)