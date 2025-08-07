from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from config import settings
from models import init_db, SessionLocal, MediaMeta
from storage import save_streamed_file

app = FastAPI()
init_db()

# CORS (필요 시)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/media",
    StaticFiles(directory=settings.STORAGE_PATH, html=True),
    name="media",
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_token(authorization: str = Header(None)):
    if authorization != f"Bearer {settings.AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing token")

@app.post("/upload_media")
async def upload_media(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(verify_token)
):
    # 미디어 타입 결정
    media_type = "image" if file.content_type.startswith("image/") else "video"

    exists = (
        db.query(MediaMeta)
            .filter(
                MediaMeta.filename == file.filename,
                MediaMeta.media_type == media_type,
                MediaMeta.status == "uploaded")
            .first()
    )
    if exists:
        return {
            "media_id": exists.id,
            "media_type": exists.media_type,
            "status": exists.status,
            "message": "이미 업로드된 파일입니다."
        }


    # 1) 메타데이터 생성
    media = MediaMeta(filename=file.filename, media_type=media_type, status="uploading")
    db.add(media); db.commit(); db.refresh(media)

    # 2) 파일 저장
    try:
        await save_streamed_file(file, media_type, media.id)
    except Exception as e:
        media.status = "failed"
        db.commit()
        raise HTTPException(500, f"Upload failed: {e}")


    # 3) 상태 갱신 및 백그라운드 작업
    media.status = "uploaded"
    db.commit()
    # if media_type == "video":
    #     background_tasks.add_task(process_video, media.id)

    return {"media_id": media.id, "media_type": media_type, "status": media.status}

@app.get("/player/{media_id}", response_class=HTMLResponse)
def player(media_id: int, db: Session = Depends(get_db)):
    media = db.query(MediaMeta).filter(MediaMeta.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    # videos 또는 images 폴더로 분기
    folder = "videos" if media.media_type == "video" else "images"
    file_url = f"/media/{folder}/{media.id}/{media.filename}"

    if media.media_type == "video":
        html = f"""
        <html>
          <body style="margin:0;display:flex;justify-content:center;align-items:center;height:100vh;background:#000">
            <!-- Progressive MP4 스트리밍: HTTP Range 요청으로 재생 -->
            <video
              src="{file_url}"
              controls autoplay
              style="max-width:100%;max-height:100%;"
            ></video>
          </body>
        </html>"""

    else:
        html = f"""
        <html><body style="margin:0;display:flex;justify-content:center;align-items:center;height:100vh;background:#333">
          <img src="{file_url}" style="max-width:100%;max-height:100%;"/>
        </body></html>"""

    return HTMLResponse(content=html, status_code=200)

#HLS 로직
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import HTMLResponse
# from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends, HTTPException, Header
# from fastapi.middleware.cors import CORSMiddleware
# from sqlalchemy.orm import Session
# from config import settings
# from models import init_db, SessionLocal, MediaMeta
# from storage import save_streamed_file
# from background import process_video

# app = FastAPI()
# init_db()

# # CORS (필요 시)
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# app.mount(
#     "/media",
#     StaticFiles(directory=settings.STORAGE_PATH, html=True),
#     name="media",
# )

# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()

# def verify_token(authorization: str = Header(None)):
#     if authorization != f"Bearer {settings.AUTH_TOKEN}":
#         raise HTTPException(status_code=401, detail="Invalid or missing token")

# @app.post("/upload_media")
# async def upload_media(
#     background_tasks: BackgroundTasks,
#     file: UploadFile = File(...),
#     db: Session = Depends(get_db),
#     _: None = Depends(verify_token)
# ):
#     # 미디어 타입 결정
#     media_type = "image" if file.content_type.startswith("image/") else "video"

#     exists = (
#         db.query(MediaMeta)
#             .filter(
#                 MediaMeta.filename == file.filename,
#                 MediaMeta.media_type == media_type,
#                 MediaMeta.status == "uploaded")
#             .first()
#     )
#     if exists:
#         return {
#             "media_id": exists.id,
#             "media_type": exists.media_type,
#             "status": exists.status,
#             "message": "이미 업로드된 파일입니다."
#         }


#     # 1) 메타데이터 생성
#     media = MediaMeta(filename=file.filename, media_type=media_type, status="uploading")
#     db.add(media); db.commit(); db.refresh(media)

#     # 2) 파일 저장
#     try:
#         await save_streamed_file(file, media_type, media.id)
#     except Exception as e:
#         media.status = "failed"
#         db.commit()
#         raise HTTPException(500, f"Upload failed: {e}")


#     # 3) 상태 갱신 및 백그라운드 작업(hls 생성 및 저장)
#     media.status = "uploaded"
#     db.commit()
#     if media_type == "video":
#         background_tasks.add_task(process_video, media.id)

#     return {"media_id": media.id, "media_type": media_type, "status": media.status}

# @app.get("/player/{media_id}", response_class=HTMLResponse)
# def player(media_id: int, db: Session = Depends(get_db)):
#     media = db.query(MediaMeta).filter(MediaMeta.id == media_id).first()
#     if not media:
#         raise HTTPException(status_code=404, detail="Media not found")

#     # videos 또는 images 폴더로 분기
#     folder = "videos" if media.media_type == "video" else "images"
#     file_url = f"/media/{folder}/{media.id}/{media.filename}"

#     if media.media_type == "video":
#         html = f"""
#         <html><body style="margin:0;display:flex;justify-content:center;align-items:center;height:100vh;background:#000">
#           <video src="{file_url}" controls autoplay style="max-width:100%;max-height:100%;"></video>
#         </body></html>"""
#     else:
#         html = f"""
#         <html><body style="margin:0;display:flex;justify-content:center;align-items:center;height:100vh;background:#333">
#           <img src="{file_url}" style="max-width:100%;max-height:100%;"/>
#         </body></html>"""

#     return HTMLResponse(content=html, status_code=200)