import json
import requests
import os
import uuid
import time
import random

COMFYUI_API_URL = "http://127.0.0.1:8188/prompt"
workflow_path = os.path.join("workflows", "testapi1.json")

with open(workflow_path, "r", encoding="utf-8") as f:
    base_prompt = json.load(f)

# ✅ 번역된 영어 프롬프트 시퀀스 (같은 카메라 앵글 강조 추가)
translated_prompts = [
    "A bright sunny morning at Gyeongbokgung Palace in Seoul, Korea, clear blue sky, traditional Korean architecture, soft morning light, shot from the same camera angle, consistent framing, timelapse sequence",
    "A bright afternoon at Gyeongbokgung Palace, strong sunlight, vivid colors, clear skies, long shadows, shot from the same camera angle, consistent framing, timelapse sequence",
    "Golden hour at Gyeongbokgung Palace, clouds forming in the sky, warm sunset glow, traditional palace architecture, shot from the same camera angle, consistent framing, timelapse sequence",
    "Twilight at Gyeongbokgung Palace, light rain falling, dim evening light, reflections on wet stone, traditional palace lights, shot from the same camera angle, consistent framing, timelapse sequence",
    "Rainy night at Gyeongbokgung Palace, dark sky, wet ground reflecting palace lights, traditional Korean architecture, shot from the same camera angle, consistent framing, timelapse sequence"
]

# ✅ 부정 프롬프트 (고정)
negative_prompt = "low quality, blurry, distorted, bad lighting, watermark, poorly drawn"

num_images = len(translated_prompts)

for i in range(num_images):
    prompt = json.loads(json.dumps(base_prompt))

    # ✅ 시드 랜덤화
    if "3" in prompt:
        prompt["3"]["inputs"]["seed"] = random.randint(1, int(1e18))

    # ✅ 프롬프트 삽입 (노드 6번: positive / 노드 7번: negative)
    if "6" in prompt:
        prompt["6"]["inputs"]["text"] = translated_prompts[i]
    if "7" in prompt:
        prompt["7"]["inputs"]["text"] = negative_prompt

    client_id = str(uuid.uuid4())

    payload = {
        "prompt": prompt,
        "client_id": client_id
    }

    print(f"\n🟡 [{i+1}/{num_images}번째 요청] client_id: {client_id}")
    print(f"📝 Prompt: {translated_prompts[i]}")

    response = requests.post(COMFYUI_API_URL, json=payload)

    if response.status_code == 200:
        result = response.json()
        prompt_id = result.get("prompt_id")
        print(f"✅ 생성 성공! Prompt ID: {prompt_id}")
        print(f"📂 결과 보기: http://127.0.0.1:8188/history/{prompt_id}")
    else:
        print(f"❌ 생성 실패 - 상태 코드: {response.status_code}")
        print("내용:", response.text)

    time.sleep(1.0)
