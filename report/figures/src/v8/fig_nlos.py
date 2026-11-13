"""그림: (가) 앵커 배치와 몸 가림, (나) 가려진 거리를 버리는 비대칭 기준."""
import math

from figlib import ACCENT, ACCENT_FILL, GROUP_FILL, INK, LINE, SUB, WARN, icon, label, render, text

W, H = 1740, 640
b = []

# ---------------- (가) 위에서 본 방
b.append(text(40, 44, "(가) 앵커 4대와 발목 태그", 29, 700, INK, "start"))
RX0, RY0, RW, RH = 40, 70, 760, 540
b.append(f'<rect x="{RX0}" y="{RY0}" width="{RW}" height="{RH}" rx="10" fill="#fbfbfc" stroke="{LINE}" stroke-width="3"/>')
anchors = {"A0(헤드)": (110, 140), "A1": (730, 140), "A2": (730, 540), "A3": (110, 540)}
tag = (386, 398)
# 걷는 길
b.append(f'<path d="M150,340 L680,340" stroke="{GROUP_FILL}" stroke-width="64" stroke-linecap="round"/>')
b.append(f'<path d="M520,340 L672,340" stroke="{SUB}" stroke-width="3" stroke-dasharray="4 10" stroke-linecap="round" marker-end="url(#aea)"/>')
# 거리 선
for name, (ax, ay) in anchors.items():
    blocked = name == "A1"
    col, dash, sw = (WARN, ' stroke-dasharray="14 9"', 4) if blocked else (ACCENT, "", 3)
    b.append(f'<line x1="{tag[0]}" y1="{tag[1]}" x2="{ax}" y2="{ay}" stroke="{col}" stroke-width="{sw}"{dash}/>')
# 사람(위에서 본 모양): 발 둘, 몸통, 머리
b.append(f'<rect x="436" y="296" width="62" height="26" rx="13" fill="#ffffff" stroke="{LINE}" stroke-width="3"/>')   # 왼발(앞)
b.append(f'<rect x="352" y="358" width="62" height="26" rx="13" fill="#ffffff" stroke="{LINE}" stroke-width="3"/>')   # 오른발(뒤)
b.append(f'<ellipse cx="432" cy="340" rx="30" ry="66" fill="#d7dbe2" stroke="{LINE}" stroke-width="3"/>')
b.append(f'<circle cx="436" cy="340" r="25" fill="#ffffff" stroke="{LINE}" stroke-width="3"/>')
# 태그
b.append(f'<circle cx="{tag[0]}" cy="{tag[1]}" r="15" fill="{ACCENT}" stroke="#ffffff" stroke-width="4"/>')
b.append(text(tag[0] + 6, tag[1] + 62, "발목 태그", 27, 600, INK))
# 앵커
for name, (ax, ay) in anchors.items():
    b.append(f'<circle cx="{ax}" cy="{ay}" r="38" fill="#ffffff" stroke="{LINE}" stroke-width="3"/>')
    b.append(icon("antenna", ax, ay, 44))
    lx, anchor = (ax + 52, "start") if ax < 400 else (ax - 52, "end")
    b.append(text(lx, ay - 26 if ay < 300 else ay + 44, name, 27, 600, INK, anchor))
b.append(text(420, 566, "직선으로 잰 거리", 25, 600, ACCENT))
b.append(text(560, 200, "몸에 가려진 거리", 25, 600, WARN, "end"))

# ---------------- (나) 선별 기준
X0, X1 = 900, 1700          # 그림 영역
b.append(text(X0, 44, "(나) 가려진 거리를 버리는 기준", 29, 700, INK, "start"))
YB = 520                    # 가로축
SMIN, SMAX = -4.5, 9.5      # σ 단위


def sx(s):
    return X0 + (s - SMIN) / (SMAX - SMIN) * (X1 - X0)


def gauss(s, mu=0.0, sd=1.0):
    return math.exp(-0.5 * ((s - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))


def nlos(s):                # U(2.3, 7.7) + N(0, 1): 가림 0.15–0.5 m를 σ ≈ 6.5 cm로 나눈 모양
    lo, hi = 2.3, 7.7
    cdf = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return (cdf(s - lo) - cdf(s - hi)) / (hi - lo)


TOP = 150
scale = (YB - TOP - 50) / gauss(0)
# 받아들이는 띠와 버리는 띠
b.append(f'<rect x="{sx(-3)}" y="{TOP - 40}" width="{sx(1.5) - sx(-3)}" height="{YB - TOP + 40}" fill="{ACCENT_FILL}"/>')
b.append(f'<rect x="{sx(1.5)}" y="{TOP - 40}" width="{X1 - sx(1.5)}" height="{YB - TOP + 40}" fill="#fdeee4"/>')
b.append(f'<rect x="{X0}" y="{TOP - 40}" width="{sx(-3) - X0}" height="{YB - TOP + 40}" fill="#fdeee4"/>')
pts = [SMIN + i * 0.05 for i in range(int((SMAX - SMIN) / 0.05) + 1)]
clean = " ".join(f"{sx(s):.1f},{YB - gauss(s) * scale:.1f}" for s in pts)
shadow = " ".join(f"{sx(s):.1f},{YB - nlos(s) * scale:.1f}" for s in pts)
b.append(f'<polygon points="{sx(SMIN):.1f},{YB} {shadow} {sx(SMAX):.1f},{YB}" fill="{WARN}" fill-opacity="0.18"/>')
b.append(f'<polyline points="{shadow}" fill="none" stroke="{WARN}" stroke-width="4"/>')
b.append(f'<polygon points="{sx(SMIN):.1f},{YB} {clean} {sx(SMAX):.1f},{YB}" fill="{ACCENT}" fill-opacity="0.16"/>')
b.append(f'<polyline points="{clean}" fill="none" stroke="{ACCENT}" stroke-width="4"/>')
# 축과 기준선
b.append(f'<line x1="{X0}" y1="{YB}" x2="{X1}" y2="{YB}" stroke="{LINE}" stroke-width="3"/>')
for s, lab in ((-3, "−3.0"), (0, "0"), (1.5, "+1.5")):
    b.append(f'<line x1="{sx(s)}" y1="{YB}" x2="{sx(s)}" y2="{YB + 12}" stroke="{LINE}" stroke-width="3"/>')
    b.append(text(sx(s), YB + 44, lab, 26, 600, INK))
for s in (-3, 1.5):
    b.append(f'<line x1="{sx(s)}" y1="{TOP - 40}" x2="{sx(s)}" y2="{YB}" stroke="{LINE}" stroke-width="3" stroke-dasharray="10 8"/>')
b.append(text((X0 + X1) / 2, YB + 92, "측정 거리 − 예측 거리 (σ 단위)", 25, 500, SUB))
b.append(text((sx(-3) + sx(1.5)) / 2, TOP - 6, "받아들임", 28, 700, ACCENT))
b.append(text((sx(1.5) + X1) / 2, TOP - 6, "버림", 28, 700, WARN))
b.append(text(sx(-2.82), 232, "가려지지", 25, 600, ACCENT, "start"))
b.append(text(sx(-2.82), 264, "않은 거리", 25, 600, ACCENT, "start"))
b.append(text(sx(5.0), 462, "가려진 거리", 25, 600, WARN))
print(render("fig_nlos", W, H, "".join(b)))
