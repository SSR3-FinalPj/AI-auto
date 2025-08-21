# # llm_client.py
# import os, json, time
# from typing import Any, Dict, Optional
# import google.generativeai as genai
# from dotenv import load_dotenv
# load_dotenv()

# # 환경변수:
# #   GOOGLE_API_KEY  (필수)
# #   GEMINI_MODEL    (선택, 기본: gemini-1.5-flash)
# #   GEMINI_RETRIES  (선택, 기본: 2)

# _API_KEY = os.getenv("GOOGLE_API_KEY")
# if not _API_KEY:
#     raise RuntimeError("GOOGLE_API_KEY is not set")
# _MODEL  = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
# _RETRY  = int(os.getenv("GEMINI_RETRIES", "2"))

# genai.configure(api_key=_API_KEY)
# _model = genai.GenerativeModel(_MODEL)

# _SYSTEM = (
#     "You are a concise data-to-text generator. "
#     "Given JSON with weather/crowd (and optionally youtube/reddit), "
#     "return 2–4 compact English sentences (<70 words) summarizing: "
#     "location + weather(temp/humidity/UV), crowd level (gender/ages if present), "
#     "and one short suggestion. No emojis/hashtags."
# )

# def _build_user_prompt(payload: Dict[str, Any]) -> str:
#     weather = payload.get("weather") or {}
#     youtube = payload.get("youtube")
#     reddit  = payload.get("reddit")
#     return "JSON:\n" + json.dumps(
#         {"weather": weather, "youtube": youtube, "reddit": reddit},
#         ensure_ascii=False
#     )

# def summarize_to_english(payload: Dict[str, Any]) -> str:
#     last_err: Optional[Exception] = None
#     for attempt in range(_RETRY + 1):
#         try:
#             resp = _model.generate_content(
#                 contents=[
#                     {"role": "system", "parts": [_SYSTEM]},
#                     {"role": "user",   "parts": [_build_user_prompt(payload)]},
#                 ]
#             )
#             text = (resp.text or "").strip()
#             if not text:
#                 raise RuntimeError("Empty response from Gemini")
#             return " ".join(text.split())  # 과도한 개행 정리
#         except Exception as e:
#             last_err = e
#             if attempt < _RETRY:
#                 time.sleep(0.6 * (attempt + 1))
#             else:
#                 raise RuntimeError(f"Gemini summarization failed: {e}") from e

# llm_client.py  (REST version; gRPC/SDK 우회)
import os, json, time
from typing import Any, Dict
import httpx
from dotenv import load_dotenv
load_dotenv()

API_KEY = (os.getenv("GOOGLE_API_KEY", "").strip().strip('"').strip("'"))
if not API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

SYSTEM = (
  "You convert JSON about local weather and crowd into exactly 3 natural English sentences. "
  "1) Location + temp(°C), humidity(%), UV(number) + a short feels-like descriptor. "
  "2) Crowd level including male/female ratios and any dominant age groups if present. "
  "3) One actionable suggestion (hydration, sun protection, walking time, etc.). "
  "12–25 words per sentence. No lists, emojis, or hashtags."
)

def _build_user_prompt(payload: Dict[str, Any]) -> str:
    weather = payload.get("weather") or {}
    youtube = payload.get("youtube")
    reddit  = payload.get("reddit")
    # 숫자 반응이 있으면 온라인 활동감 요약 힌트 제공(선택)
    hint = ""
    try:
        y = (youtube or {}).get("additionalProp1") or {}
        r = (reddit  or {}).get("additionalProp1") or {}
        likes = int(str(y.get("like","0")).strip() or 0)
        comments = int(str(y.get("comment","0")).strip() or 0)
        score = int(str(r.get("score","0")).strip() or 0)
        numc  = int(str(r.get("numcomment","0")).strip() or 0)
        if (likes+comments+score+numc) > 0:
            hint = f"\nEngagement hint: likes={likes}, comments={comments}, reddit_score={score}, reddit_comments={numc}."
    except Exception:
        pass

    return ("JSON follows. Write the 3 sentences as requested."
            f"{hint}\nJSON:\n" + json.dumps(
               {"weather": weather, "youtube": youtube, "reddit": reddit},
               ensure_ascii=False
            ))

def summarize_to_english(payload: Dict[str, Any]) -> str:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    req = {
        "contents": [
            {"role": "user", "parts": [{"text": SYSTEM}]},
            {"role": "user", "parts": [{"text": _build_user_prompt(payload)}]},
        ]
    }

    for i in range(3):
        try:
            with httpx.Client(timeout=20) as cli:
                resp = cli.post(endpoint, json=req)
            resp.raise_for_status()
            data = resp.json()
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text = " ".join(p.get("text","").strip() for p in parts if p.get("text"))
            text = " ".join(text.split()).strip()
            if not text:
                raise RuntimeError("Empty response from Gemini REST")
            return text
        except Exception as e:
            if i == 2:
                raise RuntimeError(f"Gemini REST failed: {e}") from e
            time.sleep(0.6*(i+1))
