"""그림: 가림 크기에 따른 위치 오차(합성 보행, 구역을 도는 걸음, 크기마다 10회의 중앙값)."""
import json
import statistics as st
from pathlib import Path

from chartlib import LAB, OURS, SYM, Axes
from figlib import INK, render, text

R = json.loads((Path(__file__).resolve().parents[2] / "data" / "results.json").read_text())


def rmse(cond, method):
    return [x["rmse_3d_m"] for x in R["E2_bias"]["runs"] if x["condition"] == cond and x["method"] == method]


W, H = 1740, 500
ax = Axes(170, 40, 1500, 330, (0, 105), (0, 0.30))
b = [ax.frame([(v, str(v)) for v in (0, 25, 50, 75, 100)], [(0, "0"), (0.1, "10 cm"), (0.2, "20 cm"), (0.3, "30 cm")],
              "NLOS로 거리가 길어지는 크기 (cm)")]
biases = ("0.05", "0.10", "0.15", "0.20", "0.30", "0.50", "0.75", "1.00")
for m, col in (("fusion_sym", SYM), ("fusion_asym_rts", OURS)):
    xs = [float(v) * 100 for v in biases]
    ys = [st.median(rmse(f"bias_{v}", m)) for v in biases]
    b.append(ax.line(xs, ys, col, sw=5))
    b += [ax.dot(x, y, col, r=9) for x, y in zip(xs, ys)]
b.append(text(ax.x(30) + 26, ax.y(0.268) + 8, "기존 방식의 융합(대칭 gating)", LAB, 600, INK, "start"))
b.append(text(ax.x(62), ax.y(0.017) - 26, "본 연구(비대칭 gating)", LAB, 600, INK))
print(render("fig_compare", W, H, "".join(b)))
