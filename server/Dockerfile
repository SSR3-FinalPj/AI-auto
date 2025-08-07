# 베이스 이미지
FROM python:3.10-slim

# 의존성 설치
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y ffmpeg
RUN pip install --no-cache-dir -r requirements.txt

# 코드 복사
COPY . .

# 업로드 디렉터리 확보
RUN mkdir -p /data

# 포트 열기
EXPOSE 8000

# 서버 기동
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
