import os, json, time, hmac, hashlib, threading, queue, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict

import httpx
from fastapi import FastAPI, Body, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError
from confluent_kafka import Producer
from contextlib import asynccontextmanager
from llm_client import summarize_to_english, summarize_top3_text
from dotenv import load_dotenv
load_dotenv()

# -------------------
# Settings
# -------------------
GENERATOR_ENDPOINT = os.getenv("GENERATOR_ENDPOINT")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP")
KAFKA_TOPIC      = os.getenv("KAFKA_TOPIC", "video-callback")
TTL_SECONDS      = int(os.getenv("TTL_SECONDS", "86400"))
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "1"))
SERIALIZE_BY_CALLBACK = True

print("GENERATOR_ENDPOINT =", GENERATOR_ENDPOINT)
print("KAFKA_BOOTSTRAP =", KAFKA_BOOTSTRAP)
print("KAFKA_TOPIC =", KAFKA_TOPIC)
KST = timezone(timedelta(hours=9))

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
    userId: str
    shutdown: bool = False
    weather: Weather
    youtube: Optional[Dict[str, Any]] = None
    reddit: Optional[Dict[str, Any]] = None
    user: Optional[Dict[str, Any]] = None

class Envelope(BaseModel):
    youtube: Optional[Dict[str, Any]] = None
    reddit: Optional[Dict[str, Any]]  = None
    topic: Optional[str] = None 

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
    def delivery_report(err, msg):
        if err is not None:
            print(f"[KAFKA_ERROR] Delivery failed for key={event_key}: {err}")
        else:
            print(f"[KAFKA_OK] Delivered to {msg.topic()} [{msg.partition()}] offset {msg.offset()}")

    try:
        producer.produce(
            topic=KAFKA_TOPIC,
            key=event_key,
            value=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            callback=delivery_report
        )
        producer.flush(5)
    except Exception as e:
        print(f"[KAFKA_EXCEPTION] {e}")

# -------------------
# Worker
# -------------------
def worker_loop():
    print("Worker thread started!")
    while True:
        job = job_queue.get()
        print(f"[Worker] Dequeued job {job['requestId']} for user {job['userId']}")
        attempts = job.get("_attempts", 0)
        req_id = job["requestId"]

        done_evt = threading.Event()
        try:
            with lock:
                inflight[req_id] = {
                    "userId": job["userId"],
                    "payload": job,
                    "deadline": now_utc() + timedelta(seconds=TTL_SECONDS),
                    "enqueuedAt": job.get("_enqueuedAt", now_utc().isoformat()),
                    "doneEvt": done_evt,
                }

            try:
                english_text = job.get("_englishText")
                if not english_text:
                    english_text = summarize_to_english(job)
                    job["_englishText"] = english_text
                with lock:
                    inflight[req_id]["englishText"] = english_text
                log_once(req_id, f"[LLM_OK][{req_id}] {english_text}")
            except Exception as e:
                w = job.get("weather", {})
                english_text = (
                    f"{w.get('areaName','Unknown area')}: "
                    f"{w.get('temperature','?')}°C, humidity {w.get('humidity','?')}%, "
                    f"UV {w.get('uvIndex','?')}."
                )
                job["_englishText"] = english_text
                with lock:
                    inflight[req_id]["englishText"] = english_text
                log_once(req_id, f"[LLM_FALLBACK][{req_id}] {english_text} | err={e}")

            with httpx.Client(timeout=10) as cli:
                gen_body = {
                    "requestId": req_id,
                    "userId": job["userId"],
                    "img": job.get("img"),
                    "shutdown": job.get("shutdown"),
                    "englishText": english_text,
                }
                if not GENERATOR_ENDPOINT:
                    raise RuntimeError("GENERATOR_ENDPOINT is not set")
                try:
                    r = cli.post(GENERATOR_ENDPOINT, json=gen_body)
                    r.raise_for_status()
                except Exception as ge:
                    print(f"[GEN_POST_FAIL][{req_id}] {ge}")
                    raise

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
                    "eventId": f"evt_{req_id}_bridge_fail",
                    "requestId": req_id,
                    "userId": job["userId"],
                    "status": "FAILED",
                    "message": f"bridge->generator call failed after retries: {e}",
                    "createdAt": now_utc().isoformat()
                }
                produce_kafka(event["eventId"], event)
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
                "eventId": f"evt_{r}_expired",
                "requestId": r,
                "userId": info["userId"],
                "prompt": info.get("englishText"),
                "status": "FAILED",
                "message": "callback timeout",
                "createdAt": now_utc().isoformat()
            }
            produce_kafka(event["eventId"], event)
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
        "userId": data["userId"],
        "weather": data["weather"],
        "youtube": data.get("youtube"),
        "reddit": data.get("reddit"),
        "user": data.get("user")
    })
    with lock:
        if derived_key in idemp_index:
            req_id = idemp_index[derived_key]
            return JSONResponse({"requestId": req_id, "enqueued": True, "deduplicated": True})
        req_id = make_id()
        idemp_index[derived_key] = req_id

    job = {**data, "requestId": req_id, "_enqueuedAt": now_utc().isoformat()}
    job_queue.put(job)
    return JSONResponse({"requestId": req_id, "enqueued": True, "deduplicated": False}, status_code=202)

@app.post("/api/video/callback")
async def generator_callback(request: Request):
    raw = await request.body()
    try:
        cb = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(400, f"invalid callback: {e}")

    print("DEBUG callback raw:", cb)

    with lock:
        info = inflight.get(cb.get("requestId"))
        done_evt = info.get("doneEvt") if info else None

    if info is None:
        return JSONResponse({"ok": True, "late": True})

    if done_evt:
        done_evt.set()

    with lock:
        inflight.pop(cb.get("requestId"), None)

    event = {
        "eventId": cb.get("eventId") or f"evt_{cb.get('requestId')}_bridge_fail",
        # imageKey: Generator 콜백이 없으면 Spring에서 들어온 원본 img 사용
        "imageKey": cb.get("imageKey") or info["payload"].get("img"),
        "userId": int(cb.get("userId")),
        "prompt": cb.get("prompt") or info.get("englishText"),
        # videoKey: 성공일 때만, 실패면 None
        "videoKey": cb.get("videoKey") if cb.get("status") == "SUCCESS" else "testname1557.mp4",
        "status": cb.get("status") or "FAILED",
        "message": cb.get("message") or "bridge->generator call failed after retries: ",
        "createdAt": cb.get("createdAt") or now_utc().isoformat()
    }

    produce_kafka(event["eventId"], event)

    with lock:
        completed.add(cb.get("requestId"))

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

@app.post("/api/comments", response_class=PlainTextResponse)
def comments_top3(envelope: Envelope):
    if not envelope.youtube and not envelope.reddit:
        raise HTTPException(400, "youtube 또는 reddit 중 최소 하나는 포함해야 합니다.")
    data = summarize_top3_text(envelope.model_dump())
    return JSONResponse(content=data, status_code=200)
