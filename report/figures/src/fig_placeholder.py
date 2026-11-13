"""사진과 실측 그래프가 들어갈 자리. 한글에서 그림을 골라 '그림 바꾸기'로 실제 사진·그래프로 바꿉니다."""
from figlib import GROUP_STROKE, SUB, icon, render, text


def slot(x, y, w, h, title):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#f7f8fa" stroke="{GROUP_STROKE}" '
            f'stroke-width="3" stroke-dasharray="16 12"/>'
            + icon("photo", x + w / 2, y + h / 2 - 34, 72, "#9aa1ad")
            + text(x + w / 2, y + h / 2 + 52, title, 28, 600, SUB))


W, H = 1740, 560
b = [slot(20, 20, 835, 520, "사진 자리: 발목에 찬 태그"), slot(885, 20, 835, 520, "사진 자리: 앵커를 설치한 통로")]
print(render("ph_photos", W, H, "".join(b)))
