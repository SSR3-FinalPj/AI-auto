FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# requirements 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스 복사
COPY . .

# FastAPI 실행 포트
EXPOSE 9001

# generator_server 실행 (9001 포트)
CMD ["uvicorn", "generator_server:app", "--host", "0.0.0.0", "--port", "9001"]
