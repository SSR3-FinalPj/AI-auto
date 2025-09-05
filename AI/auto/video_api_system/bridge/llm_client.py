# llm_client.py 
import re
import os, json, time
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()

SYSTEM = ('''
You are a formatter. Always write in English only. If a value is missing, say it is absent; never guess. Output is a single paragraph after my client removes delimiters.

Unit redefinition:
- Never use the word “sentence”.
- Each output is a word-block: 15–25 standalone words, separated by single spaces, not forming grammatical sentences.
- Prefer nouns/adjectives/adverbs; avoid clause markers like “that/which/because/and/so”. Do not use verbs unless absolutely needed for meaning.

Weather & Crowd (always present):
- Produce exactly 3 word-blocks.
  1) Location + temperature (°C), humidity (%), UV index + a short feels-like description.
  2) Crowd level including male/female ratios and dominant age groups if available.
  3) One actionable suggestion (hydration, sun protection, walking time, etc.).

User Notes (only if data exists):
- If the JSON field "user" is a non-empty string, produce exactly 2 additional word-blocks:
  4) Faithfully reflect the user's note; preserve key phrases; no hallucinations; translate to English if needed.
  5) One actionable suggestion tailored to the user's note.

Formatting (must follow exactly):
- Output format: <WB> ... </WB><WB> ... </WB><WB> ... </WB>[optional more]
- Each “...” is a word-block of 15–25 standalone words.
- Do not include any other text, labels, bullets, or code fences.
- Do not use the term “sentence”. If you are about to use it, replace it with “word-block”.

''').strip()

ANALYSIS = ("""
당신은 유튜브/레딧 댓글을 분석하는 인사이트 분석가입니다.
입력은 아래 스키마를 그대로 따릅니다(전처리 금지).

입력 스키마:
{
  "youtube": {
    "videoId": "string|null",
    "comments": [
      {
        "comment_id": "string",
        "author": "string|null",
        "comment": "string",
        "like_count": int,
        "total_reply_count": int,
        "published_at": "ISO8601|null"
      }, ...
    ] | null
  } | null,
  "reddit": null | {
    "postId": "string|null",
    "comments": [
      {
        "comment_id": "string",
        "author": "string|null",
        "comment": "string",
        "score": int,
        "replies": int,
        "published_at": "ISO8601|null"
      }, ...
    ] | null
  },
  "topic": "string|null"
}

규칙:
- reddit이나 youtube 중 하나가 null이면 존재하는 플랫폼만 사용하세요.
- 상위 1~3개 선택: 좋아요/점수(내림차순) → 답글수(내림차순) → 게시시각(최신 우선).
- 유튜브는 like_count/total_reply_count, 레딧은 score/replies를 사용합니다.

[출력(youtube comment가 null이 아닐 때) — 반드시 두 JSON 중 한 JSON만 반환]
{
  "video_id": "string",   // youtube.videoId가 있으면 그대로, 없으면 빈 문자열
  "top comments": [
    {
      "rank": "1",
      "platform": "youtube|reddit",
      "author": "string|null",
      "text": "string",
      "likes_or_score": "number_as_string",
      "replies": "number_as_string"
    },
    ...
  ],
  "atmosphere": "분위기를 최대 2문장으로 한국어로 요약합니다. 근거는 내용만 요약하고 author는 언급하지 않습니다."
}
            
[출력(reddit이 null이 아닐 때) - 절대 video_id 추가 금지]         
{
  "postId": "string",   //reddit.postId가 있으면 postId로 수정, 없으면 빈 문자열
  "top comments": [
    {
      "rank": "1",
      "platform": "youtube|reddit",
      "author": "string|null",
      "text": "string",
      "likes_or_score": "number_as_string",
      "replies": "number_as_string"
    },
    ...
  ],
  "atmosphere": "분위기를 최대 2문장으로 한국어로 요약합니다. 근거는 내용만 요약하고 author는 언급하지 않습니다."
}

반드시 지킬 것:
- 모든 수치 필드는 문자열(String)로 반환합니다.
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

#프롬프트 생성 함수 
def _build_user_prompt(payload: Dict[str, Any]) -> str:
    w = payload.get("weather") or {}
    u = payload.get("user") or {}

    json_payload = {
        "weather": w,
        "user":    u
    }

    # SYSTEM에 이미 전체 지시가 있음
    return (
        "JSON is provided. Follow the systemInstruction exactly. "
        "Return ONLY <WB>...</WB> blocks. "
        "Produce exactly 3 word-blocks for Weather & Crowd. "
        + ("Produce exactly 2 additional word-blocks for User Notes." if (isinstance(u, str) and u.strip()) else "There are no User Notes; do not produce them.")
        + "\nJSON:\n" + json.dumps(json_payload, ensure_ascii=False)
    )

def _to_words(s: str) -> list[str]:
    # Keep numbers and ASCII symbols like °% only if attached to tokens
    s = re.sub(r"[^\w°%\-\/]+", " ", s)         # drop punctuation except token-friendly symbols
    s = re.sub(r"\s+", " ", s).strip()
    return s.split()

def _enforce_word_blocks(text: str) -> str:
    blocks = re.findall(r"<WB>(.*?)</WB>", text, flags=re.DOTALL)
    norm_blocks = []
    for b in blocks:
        words = _to_words(b)
        if not words:
            continue
        # enforce 15–25 by trimming (never fabricate)
        if len(words) > 25:
            words = words[:25]
        elif len(words) < 15:
            # if too short, keep as-is; we prefer honesty over guessing
            pass
        norm_blocks.append(" ".join(words))
    # Final single paragraph; use em-dash separators to avoid sentence vibes
    return " — ".join(norm_blocks)

def summarize_to_english(payload: Dict[str, Any]) -> str:
    api_key = _get_api_key()
    model   = _model_name()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    req = {
    "systemInstruction": {"role": "system", "parts": [{"text": SYSTEM}]},
    "contents": [
        {"role": "user", "parts": [{"text": _build_user_prompt(payload)}]}
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
            # text = _enforce_word_blocks(text)
            # if not text:
            #     raise RuntimeError("Empty or unparsable WB output")
            # return text
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

def _normalize_to_new_schema(envelope: Dict[str, Any]) -> Dict[str, Any]:
    env = dict(envelope) if envelope else {}
    yt = (env.get("youtube") or {})
    if yt:
        # legacy: video_id -> videoId
        if "videoId" not in yt and "video_id" in yt:
            yt["videoId"] = yt.pop("video_id")
        # drop comment-level video_id (불필요)
        comments = yt.get("comments")
        if isinstance(comments, list):
            for c in comments:
                if isinstance(c, dict):
                    c.pop("video_id", None)
        env["youtube"] = yt
    return env

def _force_str(x):
    return "" if x is None else str(x)

# def _rank_candidates_from_raw(env: Dict[str, Any]) -> Dict[str, Any]:
#     yt = (env.get("youtube") or {})
#     rd = (env.get("reddit") or {})
#     video_id = yt.get("videoId") or ""
#     post_id = rd.get("postId") or ""

#     cand = []
#     for c in (yt.get("comments") or []):
#         cand.append({
#             "platform": "youtube",
#             "author": c.get("author"),
#             "text": c.get("comment"),
#             "likes_or_score": int(c.get("like_count") or 0),
#             "replies": int(c.get("total_reply_count") or 0),
#             "published_at": c.get("published_at") or ""
#         })
#     for c in (rd.get("comments") or []):
#         cand.append({
#             "platform": "reddit",
#             "author": c.get("author"),
#             "text": c.get("comment"),
#             "likes_or_score": int(c.get("score") or 0),
#             "replies": int(c.get("replies") or 0),
#             "published_at": c.get("published_at") or ""
#         })

#     # 좋아요/점수 ↓, 답글수 ↓, 게시시각 ↓(문자열이지만 ISO8601이면 문자열 비교도 최신 우선 정렬에 충분)
#     cand.sort(key=lambda x: (x["likes_or_score"], x["replies"], x["published_at"]), reverse=True)

#     top = []
#     for i, it in enumerate(cand[:3], start=1):
#         top.append({
#             "rank": _force_str(i),
#             "platform": it["platform"],
#             "author": _force_str(it.get("author")),
#             "text": _force_str(it.get("text")),
#             "likes_or_score": _force_str(it.get("likes_or_score")),
#             "replies": _force_str(it.get("replies")),
#         })

#     # 매우 단순 분위기 요약(한국어 키워드 기준)
#     texts = " ".join(t["text"] for t in top if t.get("text"))
#     pos = sum(k in texts for k in ["예뻐", "좋", "부드럽", "가독성"])
#     neg = sum(k in texts for k in ["길", "아쉽", "부족"])
#     if not texts:
#         atm = ""
#     elif pos >= neg:
#         atm = "전반적으로 긍정적이며 색감, 전환, 자막 가독성에 호평이 많습니다. 일부는 인트로 길이에 대한 개선 의견을 제시합니다."
#     else:
#         atm = "전반적으로 개선 의견이 두드러지며 특히 인트로 길이가 지적됩니다. 색감과 전환, 자막 가독성은 긍정적 평가가 있습니다."

#     return {"video_id": _force_str(video_id), "top comments": top, "atmosphere": atm}


#상위 3개 댓글 분석 
def summarize_top3_text(envelope: dict) -> dict:
    # 0) 입력 보정(레거시→신규, comment-level video_id 제거)
    envelope = _normalize_to_new_schema(envelope)

    # 1) 모델 호출
    user_prompt = json.dumps(envelope, ensure_ascii=False)
    raw = _call_gemini(ANALYSIS, user_prompt)

    # 2) JSON 추출 시도
    raw = (raw or "").strip().strip("`").strip()
    start, end = raw.find("{"), raw.rfind("}")
    data = None
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end+1])
        except Exception:
            data = None

    # 3) 정상 응답이면 키 보정 + 숫자 문자열화 + video_id 보강
    if data and isinstance(data, dict):
        top_key = "top comments" if "top comments" in data else ("top_comments" if "top_comments" in data else None)
        if top_key and isinstance(data.get(top_key), list):
            for it in data[top_key]:
                if isinstance(it, dict):
                    it["rank"] = _force_str(it.get("rank"))
                    it["likes_or_score"] = _force_str(it.get("likes_or_score"))
                    it["replies"] = _force_str(it.get("replies"))

        # 3-1) **빈 결과 보강**: top이 비었거나 atmosphere가 공백이면 로컬 폴백 사용
        # needs_fallback = (not top_key) or (not data.get(top_key)) or (not data.get("atmosphere", "").strip())
        # if needs_fallback:
        #     fb = _rank_candidates_from_raw(envelope)
        #     data.setdefault("video_id", fb["video_id"])
        #     # top 보강
        #     if (not top_key) or (not data.get(top_key)):
        #         data["top comments"] = fb["top comments"]
        #     else:
        #         # 만약 key가 top_comments였다면 일관성을 위해 "top comments"로 통일
        #         if top_key == "top_comments":
        #             data["top comments"] = data.pop("top_comments")
        #     # 분위기 보강
        #     if not data.get("atmosphere", "").strip():
        #         data["atmosphere"] = fb["atmosphere"]
        else:
            # key 통일
            if top_key == "top_comments":
                data["top comments"] = data.pop("top_comments")

        return data

    # 4) 모델이 JSON 실패 → 로컬 폴백 전면 사용
    # return _rank_candidates_from_raw(envelope)

