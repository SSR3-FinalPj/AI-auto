from sqlalchemy import Column, Integer, String, DateTime, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import datetime
from config import settings

Base = declarative_base()

class MediaMeta(Base):
    __tablename__ = "media_meta"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    media_type = Column(String, nullable=False)        # 'video' or 'image'
    status = Column(String, default="pending")
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)

# 데이터베이스 연결 및 세션팩토리
db_url = settings.DATABASE_URL
engine = create_engine(db_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# 앱 시작 시 테이블 생성
def init_db():
    Base.metadata.create_all(bind=engine)

