import os, json, time, hmac, hashlib, threading, queue, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict

import httpx
from fastapi import FastAPI, Body, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from confluent_kafka import Producer
from contextlib import asynccontextmanager
from llm_client import summarize_to_english
from dotenv import load_dotenv
load_dotenv()

# -------------------
# Settings
# -------------------
GENERATOR_ENDPOINT = os.getenv("GENERATOR_ENDPOINT")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP")
KAFKA_TOPIC      = os.getenv("KAFKA_TOPIC", "video-callback")
TTL_SECONDS      = int(os.getenv("TTL_SECONDS", "86400"))       # 24h
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "1"))
SERIALIZE_BY_CALLBACK = True

print("GENERATOR_ENDPOINT =", GENERATOR_ENDPOINT)

# Kafka 설정
producer_conf = {
    "bootstrap.servers": KAFKA_BOOTSTRAP,
    "enable.idempotence": True,
    "acks": "all",
    "retries": 5,
    "message.send.max.retries": 5,
    "socket.timeout.ms": 30000,
}
producer = Producer(producer_conf)

# -------------------
# Models
# -------------------
class Weather(BaseModel):
    areaName: str
    temperature: str
    humidity: str
    uvIndex: str
    congestionLevel: str
    maleRate: str
    femaleRate: str
    teenRate: str
    twentyRate: str
    thirtyRate: str
    fortyRate: str
    fiftyRate: str
    sixtyRate: str
    seventyRate: str

class BridgeIn(BaseModel):
    img: str
    user_id: str
    weather: Weather
    youtube: Optional[Dict[str, Any]] = None
    reddit: Optional[Dict[str, Any]] = None
    user: Optional[Dict[str, Any]] = None

class CallbackIn(BaseModel):
    request_id: str
    event_id: str
    prompt_id: Optional[str] = None
    video_id: Optional[str] = None
    prompt: Optional[str] = None
    video_path: Optional[str] = None
    video_s3_bucket: Optional[str] = None
    video_s3_key: Optional[str] = None
    video_url: Optional[str] = None
    status: str = Field(..., pattern="^(SUCCESS|FAILED)$")
    message: Optional[str] = None

# -------------------
# State
# -------------------
job_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
inflight: Dict[str, Dict[str, Any]] = {}
idemp_index: Dict[str, str] = {}
completed: set[str] = set()
printed: set[str] = set()
lock = threading.Lock()

def now_utc():
    return datetime.now(timezone.utc)

def log_once(req_id: str, msg: str):
    with lock:
        if req_id not in printed:
            print(msg)
            printed.add(req_id)

# -------------------
# Helpers
# -------------------
def make_id():
    return "req_" + uuid.uuid4().hex

def body_hash(d: dict) -> str:
    payload = json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def hmac_ok(raw_body: bytes, signature: str, secret: str) -> bool:
    mac = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    sig = signature.split("=", 1)[-1].strip() if "=" in signature else signature
    return hmac.compare_digest(mac, sig)

def produce_kafka(event_key: str, value: dict):
    producer.produce(topic=KAFKA_TOPIC, key=event_key,
                     value=json.dumps(value, ensure_ascii=False).encode("utf-8"))
    producer.flush(5)

# -------------------
# Worker
# -------------------
def worker_loop():
    print("Worker thread started!")
    while True:
        job = job_queue.get()
        print(f"[Worker] Dequeued job {job['request_id']} for user {job['user_id']}")
        attempts = job.get("_attempts", 0)
        req_id = job["request_id"]

        done_evt = threading.Event()

        try:
            # inflight 등록
            with lock:
                inflight[req_id] = {
                    "user_id": job["user_id"],
                    "payload": job,
                    "deadline": now_utc() + timedelta(seconds=TTL_SECONDS),
                    "enqueued_at": job.get("_enqueued_at", now_utc().isoformat()),
                    "done_evt": done_evt,
                }

            try:
                # 🔑 LLM 프롬프트 생성
                english_text = job.get("_english_text")
                if not english_text:
                    english_text = summarize_to_english(job)
                    job["_english_text"] = english_text
                with lock:
                    inflight[req_id]["english_text"] = english_text
                log_once(req_id, f"[LLM_OK][{req_id}] {english_text}")

            except Exception as e:
                # 실패 시 fallback
                w = job.get("weather", {})
                english_text = (
                    f"{w.get('areaName','Unknown area')}: "
                    f"{w.get('temperature','?')}°C, humidity {w.get('humidity','?')}%, "
                    f"UV {w.get('uvIndex','?')}."
                )
                job["_english_text"] = english_text
                with lock:
                    inflight[req_id]["english_text"] = english_text
                log_once(req_id, f"[LLM_FALLBACK][{req_id}] {english_text} | err={e}")

            # 제너레이터 호출
            with httpx.Client(timeout=10) as cli:
                gen_body = {
                    "request_id": req_id,
                    "user_id": job["user_id"],
                    "img": job.get("img"),
                    "english_text": english_text,
                }
                if not GENERATOR_ENDPOINT:
                    raise RuntimeError("GENERATOR_ENDPOINT is not set")
                try:
                    r = cli.post(GENERATOR_ENDPOINT, json=gen_body)
                    r.raise_for_status()
                except Exception as ge:
                    print(f"[GEN_POST_FAIL][{req_id}] {ge}")
                    raise

            # 콜백 대기
            if SERIALIZE_BY_CALLBACK:
                ok = done_evt.wait(timeout=TTL_SECONDS)
                if not ok:
                    with lock:
                        inflight.pop(req_id, None)
        except Exception as e:
            attempts += 1
            with lock:
                inflight.pop(req_id, None)
            if attempts <= 5:
                sleep_s = min(2 ** attempts, 30) + (hash(req_id) % 1000)/1000.0
                time.sleep(sleep_s)
                job["_attempts"] = attempts
                job_queue.put(job)
            else:
                event = {
                    "event_id": f"evt_{req_id}_bridge_fail",
                    "request_id": req_id,
                    "user_id": job["user_id"],
                    "status": "FAILED",
                    "message": f"bridge->generator call failed after retries: {e}",
                    "ts": now_utc().isoformat(),
                    "schema_version": 1
                }
                produce_kafka(event["event_id"], event)
        finally:
            job_queue.task_done()

def expiry_sweeper():
    while True:
        time.sleep(30)
        expired: list[str] = []
        with lock:
            for r, info in list(inflight.items()):
                if now_utc() > info["deadline"]:
                    expired.append(r)
        for r in expired:
            with lock:
                info = inflight.pop(r, None)
            if not info:
                continue
            event = {
                "event_id": f"evt_{r}_expired",
                "request_id": r,
                "user_id": info["user_id"],
                "prompt": info.get("english_text"),   #  만료 시에도 프롬프트 포함
                "status": "EXPIRED",
                "message": "callback timeout",
                "ts": now_utc().isoformat(),
                "schema_version": 1
            }
            produce_kafka(event["event_id"], event)
        if expired:
            producer.flush(5)

# -------------------
# Lifespan
# -------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("앱 시작 준비 중...")
    for _ in range(WORKER_CONCURRENCY):
        threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=expiry_sweeper, daemon=True).start()
    yield
    print("앱 종료 중... (Kafka flush)")
    try:
        producer.flush(5)
    except Exception:
        pass

app = FastAPI(title="Bridge Server", lifespan=lifespan)

# -------------------
# Endpoints
# -------------------
@app.post("/api/generate-video")
def enqueue_generate_video(
    payload: BridgeIn,
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key")
):
    try:
        data = payload.model_dump()
    except ValidationError as e:
        raise HTTPException(400, str(e))

    derived_key = idem_key or body_hash({
        "user_id": data["user_id"],
        "weather": data["weather"],
        "youtube": data.get("youtube"),
        "reddit": data.get("reddit"),
        "user": data.get("user")
    })
    with lock:
        if derived_key in idemp_index:
            req_id = idemp_index[derived_key]
            return JSONResponse({"request_id": req_id, "enqueued": True, "deduplicated": True})
        req_id = make_id()
        idemp_index[derived_key] = req_id

    job = {**data, "request_id": req_id, "_enqueued_at": now_utc().isoformat()}
    job_queue.put(job)
    return JSONResponse({"request_id": req_id, "enqueued": True, "deduplicated": False}, status_code=202)

@app.post("/api/video/callback")
async def generator_callback(request: Request):
    raw = await request.body()
    try:
        cb = CallbackIn(**json.loads(raw.decode("utf-8")))
    except Exception as e:
        raise HTTPException(400, f"invalid callback: {e}")

    with lock:
        info = inflight.get(cb.request_id)
        done_evt = info.get("done_evt") if info else None

    if info is None:
        return JSONResponse({"ok": True, "late": True})

    if done_evt:
        done_evt.set()

    with lock:
        inflight.pop(cb.request_id, None)

    #  prompt 없으면 inflight에 저장된 english_text 사용
    event = {
        "event_id": cb.event_id,
        "request_id": cb.request_id,
        "user_id": info["user_id"],
        "prompt_id": cb.prompt_id,
        "prompt": cb.prompt or info.get("english_text"),
        "video_path": cb.video_path,
        "status": cb.status,
        "message": cb.message,
        "ts": now_utc().isoformat(),
        "schema_version": 2
    }
    produce_kafka(cb.event_id, event)
    producer.flush(5)
    with lock:
        completed.add(cb.request_id)

    return JSONResponse({"ok": True, "late": False})

@app.get("/queue/stats")
def stats():
    with lock:
        return {
            "queued": job_queue.qsize(),
            "inflight": len(inflight),
            "completed": len(completed)
        }

@app.get("/healthz")
def health():
    return {"ok": True}
