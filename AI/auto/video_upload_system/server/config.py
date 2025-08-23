import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    DATABASE_URL: str 
    STORAGE_PATH: str = "/data"
    AUTH_TOKEN: str = "DEV_TOKEN"  # 클라이언트 토큰 (개발용 기본값)

    class Config:
        env_file = ".env"

settings = Settings()
