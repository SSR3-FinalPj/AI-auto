FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# confluent-kafka 구동에 필요한 OS 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates bash \
    librdkafka-dev build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스 복사
COPY . .

EXPOSE 8000 9001

CMD [ "uvicorn", "AI.bridge.app:app", "--host", "0.0.0.0", "--port", "8000"]
