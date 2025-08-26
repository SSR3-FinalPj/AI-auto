FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# confluent-kafka 구동에 필요한 OS 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates bash \
    librdkafka-dev build-essential \
 && rm -rf /var/lib/apt/lists/*

# 파이썬 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스
COPY . .

# 서비스별로 포트가 다름(브리지 9000, 프롬프트 8001)
EXPOSE 8001 9000

# 기본 엔트리는 compose에서 서비스별 command로 덮어씀
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
