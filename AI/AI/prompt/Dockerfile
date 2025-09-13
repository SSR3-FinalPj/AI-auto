FROM python:3.12-slim

# 작업 디렉토리
WORKDIR /app

# 시스템 패키지 업데이트 및 필요한 툴 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY . .

# uvicorn 실행
CMD ["uvicorn", "generator_server:app", "--host", "0.0.0.0", "--port", "9001"]

