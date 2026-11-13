"""그림: (가) 방 안을 도는 합성 보행(L0, seed 101)의 첫 바퀴를 위에서 본 궤적, (나) 120초 걷기의 위치 오차(E5, 시드 10개)."""
from pathlib import Path

import numpy as np

from chartlib import IMU, LAB, OURS, UWB, Axes, panel_title, swatch
from figlib import INK, LINE, SUB, render, text

DATA = Path(__file__).resolve().parents[2] / "data"
d = np.load(DATA / "figdata_E1_L0_seed101.npz", allow_pickle=True)
t, pt, p5, p1, tu, p2, anchors = d["t"], d["p_true"], d["p_m5"], d["p_m1"], d["t_uwb"], d["p_m2"], d["anchors"]
LAP = 13.2                                   # 정지 2초 + 첫 바퀴(활보 10개)
m, mu = t <= LAP, tu <= LAP

W, H = 1740, 690
b = [panel_title(40, 44, "(가) 위에서 본 발 궤적(첫 바퀴)")]
ax = Axes(60, 84, 760, 520, (-0.75, 5.55), (-0.55, 5.25))   # 가로 = 방의 y, 세로 = 방의 x. 1 m = 약 120 px


def xy(p):
    return p[:, 1], p[:, 0]


b.append(f'<rect x="{ax.x(0):.1f}" y="{ax.y(4):.1f}" width="{ax.x(5) - ax.x(0):.1f}" height="{ax.y(0) - ax.y(4):.1f}" '
         f'fill="#fbfbfc" stroke="{LINE}" stroke-width="2.5" stroke-dasharray="12 8"/>')
p2l = p2[mu]
ok = np.all(np.isfinite(p2l), axis=1) & (p2l[:, 1] > -0.7) & (p2l[:, 1] < 5.5) & (p2l[:, 0] > -0.5) & (p2l[:, 0] < 5.2)
for py, px in zip(*xy(p2l[ok])):
    b.append(f'<circle cx="{ax.x(py):.1f}" cy="{ax.y(px):.1f}" r="3.4" fill="{UWB}" opacity="0.5"/>')
b.append(ax.line(*xy(pt[m]), "#c3c8d0", sw=15))
b.append(ax.line(*xy(p1[m]), IMU, sw=4.5, dash="14 8"))
b.append(ax.line(*xy(p5[m]), OURS, sw=4.5))
for i, a in enumerate(anchors):
    cx, cy = ax.x(a[1]), ax.y(a[0])
    b.append(f'<rect x="{cx - 14:.1f}" y="{cy - 14:.1f}" width="28" height="28" rx="5" fill="{INK}"/>')
    b.append(text(cx + (24 if a[1] < 2.5 else -24), cy + (-22 if a[0] > 2 else 46), f"A{i}", 25, 600, INK,
                  "start" if a[1] < 2.5 else "end"))
b.append(f'<line x1="{ax.x(1.9):.1f}" y1="{ax.y(1.85):.1f}" x2="{ax.x(2.9):.1f}" y2="{ax.y(1.85):.1f}" stroke="{SUB}" stroke-width="4"/>')
b.append(text(ax.x(2.4), ax.y(1.85) + 36, "1 m", 25, 500, SUB))

# (나) 120초 동안의 위치 오차: 1초 구간의 RMS, 시드 10개의 중앙값과 10–90 백분위 띠
X0 = 930
b.append(panel_title(X0, 44, "(나) 걷는 시간에 따른 위치 오차"))
e = np.load(DATA / "figdata_E5_per_second.npz", allow_pickle=True)
ts = e["t_bin_start_s"] + 0.5
az = Axes(X0 + 110, 96, 560, 470, (0, 120), (0.008, 2.0), ylog=True)
b.append(az.frame([(s, str(s)) for s in (0, 30, 60, 90, 120)],
                  [(0.01, "1 cm"), (0.1, "10 cm"), (1.0, "1 m")], "시간 (s)"))
keep = ts >= 2.5
for key, col, dash in (("uwb_only", UWB, "3 9"), ("imu_only", IMU, "14 8"), ("fusion_asym_rts", OURS, None)):
    lo, hi, md = (np.clip(e[f"{key}_{s}_m"][keep], 0.008, 2.0) for s in ("p10", "p90", "median"))
    b.append(az.band(ts[keep], lo, hi, col))
    b.append(az.line(ts[keep], md, col, sw=5, dash=dash))
for key, name in (("uwb_only", "UWB 단독"), ("imu_only", "IMU 단독"), ("fusion_asym_rts", "본 연구")):
    yv = float(np.clip(np.median(e[f"{key}_median_m"][-10:]), 0.008, 2.0))
    b.append(text(az.x(120) + 16, az.y(yv) + 9 + (-16 if key == "imu_only" else (18 if key == "uwb_only" else 0)), name, LAB, 600, INK, "start"))

# 범례
y = H - 26
b.append(f'<line x1="70" y1="{y - 9}" x2="124" y2="{y - 9}" stroke="#c3c8d0" stroke-width="15" stroke-linecap="round"/>')
b.append(text(138, y, "참값", LAB, 600, INK, "start"))
b.append(swatch(250, y, OURS, "본 연구(융합)"))
b.append(swatch(500, y, IMU, "IMU 단독", dash="14 8"))
b.append(f'<circle cx="727" cy="{y - 9}" r="8" fill="{UWB}"/>' + text(745, y, "UWB 단독", LAB, 600, INK, "start"))
b.append(f'<rect x="{ax.x(0) - 2:.0f}" y="{ax.y(0) + 16:.0f}" width="0" height="0"/>')
print(render("fig_traj", W, H, "".join(b)))
