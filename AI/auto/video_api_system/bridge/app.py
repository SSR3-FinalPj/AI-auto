import os, json, time, hmac, hashlib, threading, queue, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict
from queue import PriorityQueue

import httpx
import asyncio
from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from confluent_kafka import Producer
from contextlib import asynccontextmanager
from llm_client import summarize_to_english, summarize_top3_text, extract_keyword, veoprompt_generate
from dotenv import load_dotenv
from models import Weather, BridgeIn, Envelope, VeoBridge
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
# State
# -------------------
job_queue: "PriorityQueue[tuple[int, dict]]" = PriorityQueue()
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
        prio, job = job_queue.get()
        print(f"[Worker] (prio={prio}) Dequeued job {job['requestId']} for user {job['jobId']}")
        attempts = job.get("_attempts", 0)
        req_id = job["requestId"]

        done_evt = threading.Event()
        try:
            with lock:
                inflight[req_id] = {
                    "jobId": job["jobId"],
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

            # 2) 제너레이터 호출 (짧은 read 타임아웃 추천)
            if not GENERATOR_ENDPOINT:
                raise RuntimeError("GENERATOR_ENDPOINT is not set")

            to = httpx.Timeout(connect=3, read=8, write=10, pool=5)
            gen_body = {
                "requestId": req_id,
                "jobId": job["jobId"],
                "platform": job.get("platform"),
                "img": job.get("img"),
                "isclient": job.get("isclient"),
                "englishText": english_text,
            }

            try:
                with httpx.Client(timeout=to) as cli:
                    r = cli.post(GENERATOR_ENDPOINT, json=gen_body)
                    if r.status_code not in (200, 201, 202):
                        raise RuntimeError(f"GEN status={r.status_code} body={r.text[:200]}")
            except httpx.ConnectError as ce:
                print(f"[GEN_CONNECT_FAIL][{req_id}] {ce}")
                raise
            except httpx.ConnectTimeout as cte:
                print(f"[GEN_CONNECT_TIMEOUT][{req_id}] {cte}")
                raise
            except httpx.ReadTimeout as rte:
                print(f"[GEN_READ_TIMEOUT][{req_id}] {rte} (proceeding; will await callback or TTL)")
                # 수락되었을 가능성이 있으니 재시도하지 않음
            except Exception as ge:
                print(f"[GEN_POST_FAIL][{req_id}] {ge}")
                raise

        except Exception as e:
            attempts += 1
            with lock:
                inflight.pop(req_id, None)
            if attempts <= 5:
                sleep_s = min(2 ** attempts, 30) + (hash(req_id) % 1000)/1000.0
                time.sleep(sleep_s)
                job["_attempts"] = attempts
                job_queue.put((prio, job))
            else:
                event = {
                    "eventId": f"evt_{req_id}_bridge_fail",
                    "requestId": req_id,
                    "jobId": job["jobId"],
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
                "jobId": info["jobId"],
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
        "jobId": data["jobId"],
        "platform": data["platform"],
        "weather": data["weather"],
        "user": data.get("user")
    })
    with lock:
        if derived_key in idemp_index:
            req_id = idemp_index[derived_key]
            return JSONResponse(
                {"requestId": req_id, "enqueued": True, "deduplicated": True},
                status_code=202
                )
        req_id = make_id()
        idemp_index[derived_key] = req_id

    job = {**data, "requestId": req_id, "_enqueuedAt": now_utc().isoformat()}

    # isclient=true → direct 처리 (LLM + inflight + generator_server 호출)
    if job.get("isclient"):
        print(f"[DIRECT] isclient=True, generator_server 직접 호출")

        done_evt = threading.Event()
        try:
            with lock:
                inflight[req_id] = {
                    "jobId": job["jobId"],
                    "payload": job,
                    "deadline": now_utc() + timedelta(seconds=TTL_SECONDS),
                    "enqueuedAt": job["_enqueuedAt"],
                    "doneEvt": done_evt,
                }

            try:
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
                    "jobId": job["jobId"],
                    "platform": job.get("platform"),
                    "img": job.get("img"),
                    "isclient": True,
                    "englishText": job["_englishText"],
                }
                if not GENERATOR_ENDPOINT:
                    raise RuntimeError("GENERATOR_ENDPOINT is not set")
                r = cli.post(GENERATOR_ENDPOINT, json=gen_body)
                r.raise_for_status()

            # if SERIALIZE_BY_CALLBACK:
            #     ok = done_evt.wait(timeout=TTL_SECONDS)
            #     if not ok:
            #         with lock:
            #             inflight.pop(req_id, None)

            return JSONResponse({"requestId": req_id, "enqueued": False, "direct": True}, status_code=202)

        except Exception as e:
            with lock:
                inflight.pop(req_id, None)
            print(f"[DIRECT_FAIL][{req_id}] {e}")
            raise HTTPException(502, f"direct call to generator failed: {e}")

    # isclient=false → 기존 큐 처리
    else:
        prio = 1
        job_queue.put((prio, job))
        return JSONResponse({"requestId": req_id, "enqueued": True, "deduplicated": False}, status_code=202)

#-------------veo3로 동영상 제작
@app.post("/api/veo3-generate")
async def enqueue_veo3_generate(
    payload: VeoBridge,
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key")
):
    try:
        data = payload.model_dump()
    except ValidationError as e:
        raise HTTPException(400, str(e))

    derived_key = idem_key or body_hash({
        "jobId": data["jobId"],
        "weather": data["weather"],
        "user": data.get("user")
    })
    with lock:
        if derived_key in idemp_index:
            req_id = idemp_index[derived_key]
            return JSONResponse(
                {"requestId": req_id, "enqueued": True, "deduplicated": True},
                status_code=202
                )
        req_id = make_id()
        idemp_index[derived_key] = req_id

    job = {**data, "requestId": req_id, "_enqueuedAt": now_utc().isoformat()}

    done_evt = threading.Event()
    with lock:
        inflight[req_id] = {
            "jobId": job["jobId"],
            "payload": job,
            "deadline": now_utc() + timedelta(seconds=TTL_SECONDS),
            "enqueuedAt": job["_enqueuedAt"],
            "doneEvt": done_evt,
        }
    try:
        extracted = await extract_keyword(job)  # llm_client의 async 함수
        if extracted is None:
            extracted = {}
    except Exception as e:
        # 실패해도 서비스는 계속 진행: 빈 dict로 응답
        print(f"[VEO3][{req_id}] extract_keyword failed: {e}")
        extracted = {}

    # 2) 백그라운드로 VEO 프롬프트 생성 → GENERATOR_ENDPOINT 전송
    async def _bg_task():
        try:
            veoprompt = await veoprompt_generate(job)  # llm_client의 async 함수
            # 제너레이터로 보낼 바디 구성 (필요 필드 포함)
            gen_body = {
                "requestId": req_id,
                "jobId": job["jobId"],
                "platform": job.get("platform"),
                "img": job.get("img"),
                "mascotimg": job.get("img"),
                "isclient": True,
                "veoPrompt": veoprompt,
            }
            if not GENERATOR_ENDPOINT:
                raise RuntimeError("GENERATOR_ENDPOINT is not set")

            # 비동기 HTTP 전송
            to = httpx.Timeout(connect=3, read=10, write=10, pool=5)
            async with httpx.AsyncClient(timeout=to) as cli:
                r = await cli.post(GENERATOR_ENDPOINT, json=gen_body)
                r.raise_for_status()
        except Exception as e:
            print(f"[VEO3_BG_FAIL][{req_id}] {e}")
        finally:
            # inflight 정리는 콜백에서 하므로 여기서는 건드리지 않음
            pass

    asyncio.create_task(_bg_task())

    # 3) 클라이언트에 먼저 응답
    return JSONResponse(
        {"requestId": req_id, "extracted": extracted},
        status_code=202
    )


#video callback api -------------------------------------------
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
        event = {
            "eventId": cb.get("eventId") or f"evt_{cb.get('requestId')}_late",
            "requestId": cb.get("requestId"),
            "jobId": cb.get("jobId"),
            "prompt": cb.get("prompt"),
            "type": cb.get("type") or "unknown",
            "resultKey": cb.get("resultKey") if cb.get("status") == "SUCCESS" else None,
            "status": cb.get("status") or "FAILED",
            "message": cb.get("message") or "late callback",
            "createdAt": cb.get("createdAt") or now_utc().isoformat()
        }
        produce_kafka(event["eventId"], event)
        return JSONResponse({"ok": True, "late": True})

    if done_evt:
        done_evt.set()

    with lock:
        inflight.pop(cb.get("requestId"), None)
        
    cb_type = (cb.get("type") or "").lower().strip()
    if cb_type in ("video", "image"):
        event_type = cb_type
    else:
        platform = (info["payload"] or {}).get("platform")
        event_type = (
            "video" if platform == "youtube"
            else "image" if platform == "reddit"
            else (cb_type or platform)
        )

    event = {
        "eventId": cb.get("eventId") or f"evt_{cb.get('requestId')}_bridge_fail",
        "imageKey": cb.get("imageKey") or info["payload"].get("img"),
        "jobId": int(cb.get("jobId")),
        "prompt": cb.get("prompt") or info.get("englishText"),
        "type": event_type,
        "resultKey": cb.get("resultKey") if cb.get("status") == "SUCCESS" else None,
        "status": cb.get("status") or "FAILED",
        "message": cb.get("message") or "bridge->generator call failed after retries: ",
        "createdAt": cb.get("createdAt") or now_utc().isoformat()
    }

    produce_kafka(event["eventId"], event)

    with lock:
        completed.add(cb.get("requestId"))

    return JSONResponse({"ok": True, "late": False})

#queue 상태 --------------------------------
@app.get("/queue/stats")
def stats():
    with lock:
        return {
            "queued": job_queue.qsize(),
            "inflight": len(inflight),
            "completed": len(completed)
        }
#상태 -------------------------------------
@app.get("/healthz")
def health():
    return {"ok": True}

#댓글분석 api ----------------------------------
@app.post("/api/comments")
def comments_top3(envelope: Dict[str, Any]):
    if not envelope:
        raise HTTPException(400, "데이터는 Dictionary 형태여야 합니다.")
    data = summarize_top3_text(envelope)
    return JSONResponse(content=data, status_code=200)
