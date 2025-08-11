import os
import imageio
from natsort import natsorted

# ✅ 정확한 이미지 폴더 경로
image_folder = r"D:\ComfyUI\ComfyUI\output"
output_video = "slideshow.mp4"
frame_duration = 0.5  # 각 이미지당 보여주는 시간 (초)
fps = 1 / frame_duration

# === 디버깅 출력 ===
print("📂 이미지 폴더 경로:", image_folder)
if not os.path.exists(image_folder):
    raise FileNotFoundError(f"❌ 폴더가 존재하지 않습니다: {image_folder}")

# === 이미지 파일 목록 불러오기
images = [f for f in os.listdir(image_folder) if f.endswith(".png")]
images = natsorted(images)
image_paths = [os.path.join(image_folder, f) for f in images]

if not images:
    raise RuntimeError("❌ PNG 이미지가 폴더에 없습니다.")

# === 슬라이드쇼 영상 생성
writer = imageio.get_writer(output_video, fps=fps)

for img_path in image_paths:
    image = imageio.imread(img_path)
    writer.append_data(image)
    print(f"🖼️ 추가됨: {img_path}")

writer.close()
print(f"\n✅ 슬라이드쇼 영상 생성 완료: {output_video}")
