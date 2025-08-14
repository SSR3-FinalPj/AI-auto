FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000
EXPOSE 8001

# 기본은 브리지(app.py)를 8000에서 구동
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
