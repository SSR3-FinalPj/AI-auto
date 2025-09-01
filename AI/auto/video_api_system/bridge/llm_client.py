# llm_client.py 
import os, json, time
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

SYSTEM = ('''
You convert JSON into natural English sentences.

Weather & Crowd (always present):
- Write 3 sentences.
1) Location + temperature (°C), humidity (%), UV index + a short feels-like description.
2) Crowd level including male/female ratios and dominant age groups if available.
3) One actionable suggestion (hydration, sun protection, walking time, etc.).
Each sentence should be 15–25 words. No lists, emojis, or hashtags.

YouTube, Reddit, and User (only if data exists):
- Write up to 4 additional sentences (one per item).
1) YouTube: include video_id, view_count, like_count, comment_count, and a brief engagement trend with a feels-like description.
2) Reddit: include video_id, score, upvotes_estimated, downvotes_estimated, num_comments, and the general tone of comments.
3) User: reflect user notes or preferences as the highest priority in wording.
4) Provide one actionable suggestion to improve engagement (e.g., encourage likes, comments, or views).
Each sentence should be 15–25 words. No lists, emojis, or hashtags.
''').strip()

ANALYSIS = ("""
당신은 유튜브 혹은 레딧 '댓글'을 분석하는 인사이트 분석가입니다.
입력 items는 이미 상위 3개(top3)로 정제되어 있습니다. 순서를 바꾸지 말고 그대로 사용하세요.
허위 추론 금지, 불확실하면 언급하지 마세요. 한국어로 답변합니다.

items: [
  { "platform":"youtube|reddit", "id":"...", "author":"...", "text":"...", "likes_or_score":int, "replies":int, "published_at": "ISO8601|null" },
  ...
]
context: { "topic":"string|null" }

[출력 형식 — 반드시 이 JSON만 반환]
{
  "top3": [
    {
      "rank": "1",
      "platform": "youtube|reddit",
      "author": "string|null",
      "text": "string",
      "likes_or_score": "number_as_string",
      "replies": "number_as_string"
    },
    {
      "rank": "2",
      "platform": "youtube|reddit",
      "author": "string|null",
      "text": "string",
      "likes_or_score": "number_as_string",
      "replies": "number_as_string"
    },
    {
      "rank": "3",
      "platform": "youtube|reddit",
      "author": "string|null",
      "text": "string",
      "likes_or_score": "number_as_string",
      "replies": "number_as_string"
    }
  ],
  "atmosphere": "분위기 해석을 최대 2문장으로 한국어로 서술합니다. 주요 근거는 내용만 요약하고 author는 언급하지 않습니다."
}
규칙:
- 모든 수치 필드(likes_or_score, replies, rank)는 문자열(String)로 반환합니다.
- JSON 이외의 텍스트/코드블록/주석/접두·접미 문구를 절대 포함하지 마세요.
""").strip()

def _get_api_key() -> str:
    # import 시점이 아닌 호출 시점에 키를 읽어 예외를 뒤로 미룸
    key = (os.getenv("GOOGLE_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    # 제어문자 제거 방지
    if any(ord(c) < 32 for c in key):
        raise RuntimeError("GOOGLE_API_KEY contains control characters")
    return key

#모델명. 
def _model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

#표준화 
def _normalize_social(payload: Dict[str, Any]) -> Dict[str, Any]:
    y = ((payload.get("youtube") or {}).get("additionalProp1") or {}).copy()
    r = ((payload.get("reddit")  or {}).get("additionalProp1") or {}).copy()
    u = ((payload.get("user")    or {}).get("additionalProp1") or {}).copy()

    # YouTube 키 표준화
    y["video_id"]      = y.get("video_id")
    y["view_count"]    = y.get("view_count") or y.get("views")
    y["like_count"]    = y.get("like_count") or y.get("likes") or y.get("like")
    y["comment_count"] = y.get("comment_count") or y.get("comments_count") or y.get("comment")
    if isinstance(y.get("comments"), str):
        y["sample_comment"] = y["comments"]

    # Reddit 키 표준화
    r["video_id"]             = r.get("video_id")
    r["score"]                = r.get("score")
    r["upvotes_estimated"]    = r.get("upvotes_estimated") or r.get("upvotes")
    r["downvotes_estimated"]  = r.get("downvotes_estimated") or r.get("downvotes")
    r["num_comments"]         = r.get("num_comments") or r.get("numcomment")
    if isinstance(r.get("comments"), str):
        r["sample_comment"] = r["comments"]

    # User 노트
    user_notes = u.get("notes") or u.get("note")

    return {"youtube": y or None, "reddit": r or None, "user_notes": user_notes}

#프롬프트 생성 함수 
def _build_user_prompt(payload: Dict[str, Any]) -> str:
    w = payload.get("weather") or {}
    social = _normalize_social(payload)

    # 참여도 힌트(있으면)
    hint = ""
    try:
        likes = int(str((social["youtube"] or {}).get("like_count",    "0")))
        cmt   = int(str((social["youtube"] or {}).get("comment_count", "0")))
        views = int(str((social["youtube"] or {}).get("view_count",    "0")))
        score = int(str((social["reddit"]  or {}).get("score",         "0")))
        rcmts = int(str((social["reddit"]  or {}).get("num_comments",  "0")))
        if (likes + cmt + views + score + rcmts) > 0:
            hint = f"\nEngagement hint: yt_views={views}, yt_likes={likes}, yt_comments={cmt}, reddit_score={score}, reddit_comments={rcmts}."
    except Exception:
        pass

    json_payload = {
        "weather": w,
        "youtube": social["youtube"],
        "reddit":  social["reddit"],
        "user":    {"notes": social["user_notes"]} if social["user_notes"] else None
    }

    # SYSTEM에 이미 전체 지시가 있음
    return (
        "JSON is provided. Follow the SYSTEM instructions above for weather/crowd and for social if present."
        f"{hint}\nJSON:\n" + json.dumps(json_payload, ensure_ascii=False)
    )

def summarize_to_english(payload: Dict[str, Any]) -> str:
    api_key = _get_api_key()
    model   = _model_name()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    req = {
        "contents": [
            {"role": "user", "parts": [{"text": SYSTEM}]},
            {"role": "user", "parts": [{"text": _build_user_prompt(payload)}]},
        ]
    }

    last_err: Optional[Exception] = None
    for i in range(3):
        try:
            with httpx.Client(timeout=20) as cli:
                resp = cli.post(endpoint, json=req)
            resp.raise_for_status()
            data = resp.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text  = " ".join(p.get("text","").strip() for p in parts if p.get("text"))
            text  = " ".join(text.split()).strip()
            if not text:
                raise RuntimeError("Empty response from Gemini REST")
            return text
        except Exception as e:
            last_err = e
            if i < 2:
                time.sleep(0.6*(i+1))
            else:
                raise RuntimeError(f"Gemini REST failed: {e}") from e

#댓글에 관한 gemini api call (통합 고려)    
def _call_gemini(promptA: str, promptB: str) -> str:
    api_key = _get_api_key()
    model   = _model_name()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = {"contents": [{"role":"user","parts":[{"text":promptA}]},
                        {"role":"user","parts":[{"text":promptB}]}]}
    last_err: Optional[Exception] = None
    for i in range(3):
        try:
            with httpx.Client(timeout=30) as cli:
                resp = cli.post(endpoint, json=req)
            resp.raise_for_status()
            data = resp.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text  = " ".join(p.get("text","").strip() for p in parts if p.get("text"))
            return " ".join(text.split()).strip()
        except Exception as e:
            last_err = e
            if i < 2:
                time.sleep(0.5 * (i+1))
            else:
                raise RuntimeError(f"Gemini REST failed: {e}") from e

#상위 3개 댓글 분석 
def summarize_top3_text(envelope: dict) -> str:
    """
    envelope: {"youtube": {...} | None, "reddit": {...} | None, "topic": str|None}
    """
    user_prompt = json.dumps(envelope, ensure_ascii=False)
    raw = _call_gemini(ANALYSIS, user_prompt)

    # 1) 코드펜스/앞뒤 잡음 제거 + 중괄호 영역만 추출
    raw = (raw or "").strip().strip("`").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("Model did not return JSON")

    payload = raw[start:end+1]

    # 2) JSON 파싱
    data = json.loads(payload)

    # 3) 스키마 보정: rank/likes_or_score/replies를 문자열로 강제 (의도한 출력 규칙)
    def _force_str(x):
        return "" if x is None else str(x)

    if isinstance(data.get("top3"), list):
        for it in data["top3"]:
            if isinstance(it, dict):
                it["rank"] = _force_str(it.get("rank"))
                it["likes_or_score"] = _force_str(it.get("likes_or_score"))
                it["replies"] = _force_str(it.get("replies"))

    return data
