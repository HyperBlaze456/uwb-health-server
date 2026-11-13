"""그림: 장치 사진 다섯 장을 한 줄로. 화면 글자가 바로 서게 돌리고 장치 둘레로 4:3으로 자릅니다."""
from pathlib import Path

from PIL import Image, ImageOps

from figlib import INK, OUT, render, text

PHOTOS = Path(__file__).resolve().parents[2] / "photos"
# (파일, 돌리는 각도(반시계), 돌리기 전 화면에서 자를 상자(x0, y0, x1, y1), 이름)
ITEMS = [
    ("p005.jpg", 0, (0, 100, 1400, 1150), "발목 태그"),
    ("p004.jpg", 90, (197, 338, 917, 1298), "헤드(A0)"),
    ("p002.jpg", 90, (195, 388, 870, 1288), "앵커 A1"),
    ("p003.jpg", 180, (0, 225, 1100, 1050), "앵커 A2"),
    ("p001.jpg", 90, (262, 290, 1050, 1340), "앵커 A3"),
]
TW, TH, GAP = 332, 249, 20
tiles = OUT / "tiles"
tiles.mkdir(parents=True, exist_ok=True)
b = []
for i, (name, rot, box, label) in enumerate(ITEMS):
    im = ImageOps.exif_transpose(Image.open(PHOTOS / name)).crop(box)
    if rot:
        im = im.rotate(rot, expand=True)
    im = ImageOps.fit(im, (TW * 3, TH * 3), Image.LANCZOS)
    path = tiles / f"tile{i}.jpg"
    im.convert("RGB").save(path, quality=90)
    x = i * (TW + GAP)
    b.append(f'<clipPath id="c{i}"><rect x="{x}" y="0" width="{TW}" height="{TH}" rx="10"/></clipPath>')
    b.append(f'<image href="{path.as_uri()}" x="{x}" y="0" width="{TW}" height="{TH}" clip-path="url(#c{i})"/>')
    b.append(text(x + TW / 2, TH + 38, label, 28, 600, INK))
print(render("fig_photos", 5 * TW + 4 * GAP, TH + 54, "".join(b)))
