# llm_client.py  — REST 버전 + lazy key + 소셜/유저 반영 + 안정 프롬프트
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

def _get_api_key() -> str:
    # import 시점이 아닌 호출 시점에 키를 읽어 예외를 뒤로 미룹니다.
    key = (os.getenv("GOOGLE_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    # 제어문자 제거 방지
    if any(ord(c) < 32 for c in key):
        raise RuntimeError("GOOGLE_API_KEY contains control characters")
    return key

def _model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

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

    # SYSTEM에 이미 전체 지시가 있으므로 여기서는 “3문장만” 같은 제약을 다시 쓰지 않습니다.
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
