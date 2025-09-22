# llm_client.py 
import re
import os, json, time
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

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

KEYWORD = ("""
당신은 사용자의 의도를 파악하는 전문가입니다. 
사용자의 의도를 파악하기 위해 아래의 내용을 익히세요

프롬프트 작성 기본사항
유용한 프롬프트는 설명적이고 명확합니다. Veo를 최대한 활용하려면 먼저 핵심 아이디어를 파악하고, 키워드와 수정자를 추가하여 아이디어를 조정하고, 동영상 관련 용어를 프롬프트에 포함하세요.
# Veo 프롬프트 구성 요소 설명
## 1. `camera_motion` (카메라 움직임 / 촬영 기법)
- 장면을 어떤 **카메라 앵글과 움직임**으로 담을지를 지정합니다.  
- 예시:
  - `Extreme Close-Up` → 피사체를 아주 가까이서 촬영 (눈, 손가락 같은 디테일 강조)  
  - `Bird’s-Eye View` → 하늘에서 내려다보는 앵글  
  - `Pan (left)` → 카메라가 왼쪽으로 부드럽게 움직임  
  - `Dolly (In)` → 카메라가 피사체 쪽으로 다가옴 (줌과는 다름)  
👉 즉, 영화적 장면에서 **카메라의 시선**을 컨트롤하는 요소예요.
## 2. `subject_animation` (주제/인물 애니메이션)
- 이미지 속 **주인공(사람, 동물, 물체 등)**이 어떤 식으로 미세하게 움직이는지 지정합니다.  
- 예시:
  - `"None"` → 정적인 상태  
  - `"The subject's head turns slowly"` → 피사체가 천천히 고개를 돌림  
  - `"The subject blinks slowly"` → 천천히 눈을 깜박임  
  - `"The subject's hair and clothes flutter gently in the wind"` → 바람에 의해 머리카락/옷이 살짝 흔들림  
👉 즉, 정지 이미지를 약간의 **생동감**을 주는 연출이에요.
## 3. `environmental_animation` (환경 애니메이션)
- 배경이나 주위 환경에서 일어나는 **움직임/변화**를 설정합니다.  
- 예시:
  - `"Fog rolls in slowly"` → 안개가 천천히 깔림  
  - `"Rain starts to fall gently"` → 빗방울이 잔잔하게 떨어짐  
  - `"Leaves rustle in the wind"` → 바람에 나뭇잎이 흔들림  
  - `"Light changes subtly"` → 조명이 부드럽게 변함  
👉 즉, 장면을 더 **영화적이고 몰입감 있게** 만드는 효과예요.
## 4. `sound_effects` (사운드 효과)
- 장면에 맞는 **소리/배경음**을 추가합니다.  
- 예시:
  - `"Sound of a phone ringing"` → 전화 벨 소리  
  - `"Waves crashing"` → 파도 부딪히는 소리  
  - `"Ticking clock"` → 시계 초침 소리  
  - `"Quiet office hum"` → 사무실의 잔잔한 소음  
👉 즉, 시각뿐 아니라 **청각적 분위기까지 보강**해주는 옵션이에요.
## 5. `dialogue` (대사 / 대화)
- 장면 속 인물이나 내레이션이 말하는 **대사**를 직접 지정합니다.  
- 사용자가 문자열을 입력하면 프롬프트에 대사가 포함되어, 인물이 말하거나 화면에 자막처럼 나타나는 효과를 줄 수 있습니다.  
- 예시:
  - `"We have to leave now."` → 인물이 긴박하게 말하는 대사  
  - `"Welcome to the future."` → 내레이션 혹은 자막 같은 효과  
👉 즉, 장면에 **스토리와 감정**을 더하는 요소예요.

[1번 요구사항을 최우선순위로 두고 출력 형식을 채우세요.]
1.user가 요구하는 내용이 프롬프트 요소 중 1번부터 7번까지 어디에 포함되는지 분석해서 출력 형식에 삽입하세요.(한개 이상 선택하여 입력된 내용의 키워드를 적으세요.)
2.출력 형식의 null 값 중에 element에 null값이 아닌 내용이 있다면 채우세요.
[반드시 지킬 것]
- user가 요구하는 내용을 추론 없이, 단어나 문자를 추가하지 말고 작성하세요.
           
[출력 형식]
{
    "camera_motion":"string|null"
    "subject_animation":"string|null"
    "environmental_animation":"string|null"
    "sound_effects":"string|null"
    "dialogue":"string|null"
    "beforeprompt":"string|null"
}

""").strip()

VEO = ("""
당신은 입력된 데이터를 최상의 프롬프트로 변환하는 프롬프트 전문가입니다. 
프롬프트를 작성하기 위해 아래의 규칙을 확인하세요

프롬프트 작성 기본사항
유용한 프롬프트는 설명적이고 명확합니다. Veo를 최대한 활용하려면 먼저 핵심 아이디어를 파악하고, 키워드와 수정자를 추가하여 아이디어를 조정하고, 동영상 관련 용어를 프롬프트에 포함하세요.
# Veo 프롬프트 구성 요소 설명
## 1. `camera_motion` (카메라 움직임 / 촬영 기법)
- 장면을 어떤 **카메라 앵글과 움직임**으로 담을지를 지정합니다.  
- 예시:
  - `Extreme Close-Up` → 피사체를 아주 가까이서 촬영 (눈, 손가락 같은 디테일 강조)  
  - `Bird’s-Eye View` → 하늘에서 내려다보는 앵글  
  - `Pan (left)` → 카메라가 왼쪽으로 부드럽게 움직임  
  - `Dolly (In)` → 카메라가 피사체 쪽으로 다가옴 (줌과는 다름)  
👉 즉, 영화적 장면에서 **카메라의 시선**을 컨트롤하는 요소예요.
## 2. `subject_animation` (주제/인물 애니메이션)
- 이미지 속 **주인공(사람, 동물, 물체 등)**이 어떤 식으로 미세하게 움직이는지 지정합니다.  
- 예시:
  - `"None"` → 정적인 상태  
  - `"The subject's head turns slowly"` → 피사체가 천천히 고개를 돌림  
  - `"The subject blinks slowly"` → 천천히 눈을 깜박임  
  - `"The subject's hair and clothes flutter gently in the wind"` → 바람에 의해 머리카락/옷이 살짝 흔들림  
👉 즉, 정지 이미지를 약간의 **생동감**을 주는 연출이에요.
## 3. `environmental_animation` (환경 애니메이션)
- 배경이나 주위 환경에서 일어나는 **움직임/변화**를 설정합니다.  
- 예시:
  - `"Fog rolls in slowly"` → 안개가 천천히 깔림  
  - `"Rain starts to fall gently"` → 빗방울이 잔잔하게 떨어짐  
  - `"Leaves rustle in the wind"` → 바람에 나뭇잎이 흔들림  
  - `"Light changes subtly"` → 조명이 부드럽게 변함  
👉 즉, 장면을 더 **영화적이고 몰입감 있게** 만드는 효과예요.
## 4. `sound_effects` (사운드 효과)
- 장면에 맞는 **소리/배경음**을 추가합니다.  
- 예시:
  - `"Sound of a phone ringing"` → 전화 벨 소리  
  - `"Waves crashing"` → 파도 부딪히는 소리  
  - `"Ticking clock"` → 시계 초침 소리  
  - `"Quiet office hum"` → 사무실의 잔잔한 소음  
👉 즉, 시각뿐 아니라 **청각적 분위기까지 보강**해주는 옵션이에요.
## 5. `dialogue` (대사 / 대화)
- 장면 속 인물이나 내레이션이 말하는 **대사**를 직접 지정합니다.  
- 사용자가 문자열을 입력하면 프롬프트에 대사가 포함되어, 인물이 말하거나 화면에 자막처럼 나타나는 효과를 줄 수 있습니다.  
- 예시:
  - `"We have to leave now."` → 인물이 긴박하게 말하는 대사  
  - `"Welcome to the future."` → 내레이션 혹은 자막 같은 효과  
👉 즉, 장면에 **스토리와 감정**을 더하는 요소예요.

1번이 최우선순위, 그 다음 숫자로 갈수록 우선순위가 낮아집니다.
[extract]
1. "null값이 아닌 요소들을 그대로 단어 형태로 작성하세요"
2. "null값인 요소들을 sample의 요소로 채워넣으세요"
3. "아직 null값인 요소들을 beforeprompt와 겹치지 않는 임의의 단어로 채워넣으세요."
[Weather(Only if data exists)]
1. "날씨 데이터를 작성하세요."

[출력 형식 : text로 전송]

"element"
"camera_motion":"string|null"
"subject_animation":"string|null"
"environmental_animation":"string|null"
"sound_effects":"string|null"
"dialogue":"string|null"
"beforeprompt":"string|null"
"weather"
"1. areaName:(string|null), temperature:(string|null),humidity: (string|null), uvIndex: (string|null)"
"2. congestionLevel: (string|null), maleRate: (string|null), femaleRate: (string|null)"
"3. teenRate": (string|null), twentyRate:(string|null), thirtyRate: (string|null), fortyRate: (string|null), fiftyRate: (string|null), sixtyRate: (string|null),seventyRate: (string|null)


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
            text = _enforce_word_blocks(text)
            if not text:
                raise RuntimeError("Empty or unparsable WB output")
            return text
            # if not text:
            #     raise RuntimeError("Empty response from Gemini REST")
            # return text
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

        else:
            # key 통일
            if top_key == "top_comments":
                data["top comments"] = data.pop("top_comments")

        return data

async def extract_keyword(input: Dict[str, Any]) -> dict:
    api_key = _get_api_key()
    model   = _model_name()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"


    inp = dict(input) if input else {}
    user = (inp.get("user") or {})
    el = (inp.get("element") or {})
    be = (inp.get("beforeprompt") or {})

    json_payload = {
        "user":user,
        "element":el,
        "beforeprompt":be
    }
    
    req = {
    "systemInstruction": {"role": "system", "parts": [{"text": KEYWORD}]},
    "contents": [
        {"role": "user", "parts": [{"text":json.dumps(json_payload, ensure_ascii=False)}]}
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
            break
        except Exception as e:
            if i < 2:
                time.sleep(0.6*(i+1))
            else:
                raise RuntimeError(f"Gemini REST failed: {e}") from e

    # 2) JSON 추출 시도
    text = (text or "").strip().strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    parsed = {}
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end+1])
        except Exception:
            parsed = {}

    keys = ["camera_motion","subject_animation","environmental_animation","sound_effects","dialogue","beforeprompt"]
    return {k: (parsed.get(k) if isinstance(parsed.get(k), str) and parsed.get(k).strip() else "null") for k in keys}

def _guess_mime_from_name(name: str) -> str:
    n = (name or "").lower()
    if n.endswith(".png"):  return "image/png"
    if n.endswith(".jpg") or n.endswith(".jpeg"): return "image/jpeg"
    if n.endswith(".webp"): return "image/webp"
    if n.endswith(".gif"):  return "image/gif"
    return "application/octet-stream"

async def veoprompt_generate(payload: Dict[str, Any]) -> str:
    api_key = _get_api_key()
    model   = _model_name()

    Di = dict(payload) if payload else {}
    extract = Di.get("_extracted")
    if extract is None:
        extract = await extract_keyword(Di)

    if not isinstance(extract, dict):
        extract = {}

    def _s(x):  # stringifier
        return str(x) if x is not None else ""

    # weather/sample/beforeprompt 문자열화(키워드로 쓸 때만 간단 요약)
    def _compact(obj):
        if obj is None:
            return ""
        if isinstance(obj, (str, int, float)):
            return _s(obj)
        if isinstance(obj, dict):
            # 중요한 값만 간단히 합치기
            # 예: {"areaName":"용산","temperature":"23","uvIndex":"3"} → "용산 23 3"
            vals = [str(v) for v in obj.values() if isinstance(v, (str,int,float)) and str(v).strip()]
            return " ".join(vals)[:200]
        if isinstance(obj, list):
            vals = [str(v) for v in obj if isinstance(v, (str,int,float)) and str(v).strip()]
            return " ".join(vals)[:200]
        return ""

    before_prompt = _compact(Di.get("beforeprompt"))
    weather_compact = _compact(Di.get("weather"))
    sample_compact  = _compact(Di.get("sample"))


    camera_motion = _s(extract.get("camera_motion")).strip() or "" 

    subject_animation = _s(extract.get("subject_animation")).strip() or "" 
    environmental_animation = _s(extract.get("environmental_animation")).strip() or "" 

    sound_effects = _s(extract.get("sound_effects")).strip() or "" 
    dialogue = _s(extract.get("dialogue")).strip() or "" 

    starting_image = _s(Di.get("img")).strip() or ""

    prompt = ""

    keywords = []
    optional_keywords = [
        camera_motion,
        subject_animation,
        environmental_animation,
        sound_effects,
        weather_compact,
        sample_compact,
        before_prompt
    ]
    for keyword in optional_keywords:
        if keyword != "None":
            keywords.append(keyword)
    if dialogue != "":
        keywords.append(dialogue)

    # 2) 이미지가 있으면: SDK로 이미지+키워드 기반 프롬프트 생성 시도
    if starting_image:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            mime = _guess_mime_from_name(starting_image)
            with open(starting_image, "rb") as f:
                img_bytes = f.read()

            gemini_model = _model_name() or "gemini-2.5-flash"
            gemini_prompt = (
                "You are an expert prompt engineer for Google's Veo model. "
                "Analyze the provided image and combine its content with the following motion and audio keywords "
                "to generate a single, cohesive, and cinematic prompt. "
                "Integrate the image's subject and scene with the requested motion and audio effects. "
                "The final output must be ONLY the prompt itself, with no preamble. "
                f"Mandatory Keywords: {', '.join(keywords)}"
            )

            response = client.models.generate_content(
                model=gemini_model,
                contents=[gemini_prompt, types.Part.from_bytes(data=img_bytes, mime_type=mime)],
            )
            prompt = (getattr(response, "text", "") or "").strip()
            if prompt:
                return prompt
        except Exception:
            # SDK/파일 문제 시 폴백으로 내려감
            pass
    # 3) 폴백: 이미지 없거나 SDK 실패 → 기존 REST 경로로 VEO 지시문 조합
    # extracted는 dict/str 모두 가능 → 문자열화
    extracted_text = json.dumps(extract, ensure_ascii=False) if isinstance(extract, dict) else _s(extract)

    json_payload = {
        "beforeprompt": Di.get("beforeprompt") or {},
        "weather": Di.get("weather") or {},
        "sample": Di.get("sample") or {}
    }

    model   = _model_name()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = {"contents": [
        {"role":"user","parts":[{"text": VEO}]},
        {"role":"user","parts":[{"text": extracted_text}]},
        {"role":"user","parts":[{"text": json.dumps(json_payload, ensure_ascii=False)}]}
    ]}

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

            pf = data.get("promptFeedback") or {}
            if not text and pf.get("blockReason"):
                raise RuntimeError(f"Gemini blocked: {pf.get('blockReason')}")

            if not text:
                raise RuntimeError("Empty response from Gemini REST")
            return text
        except Exception as e:
            last_err = e
            if i < 2:
                time.sleep(0.6 * (i + 1))
            else:
                raise RuntimeError(f"Gemini REST failed: {e}") from e

        # req = {"contents": [{"role":"user","parts":[{"text":VEO}]},
        #                     {"role":"user","parts":[{"text":json.dumps(extract, ensure_ascii=False)}]},
        #                     {"role":"user","parts":[{"text":json.dumps(json_payload, ensure_ascii=False)}]}
        #                     ]}

        # last_err: Optional[Exception] = None
        # for i in range(3):
        #     try:
        #         with httpx.Client(timeout=20) as cli:
        #             resp = cli.post(endpoint, json=req)
        #         resp.raise_for_status()
        #         data = resp.json()
        #         parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
        #         text  = " ".join(p.get("text","").strip() for p in parts if p.get("text"))
        #         text  = " ".join(text.split()).strip()

        #         # 2) safety block 여부 확인 (있으면 원인 로그)
        #         pf = data.get("promptFeedback") or {}
        #         if not text and pf.get("blockReason"):
        #             raise RuntimeError(f"Gemini blocked: {pf.get('blockReason')}")

        #         # 3) 빈 응답 방어
        #         if not text:
        #             # 원문 일부라도 로깅해서 추적
        #             raise RuntimeError("Empty response from Gemini REST")

        #         return text
