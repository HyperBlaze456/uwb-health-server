"""보고서 그래프의 공통 부품(SVG 직접 작성). 색은 dataviz 기준 팔레트의 앞 네 자리(검증기 통과)입니다.

인쇄물이므로 색만으로 구분하지 않게 선 모양(실선·파선)과 직접 붙인 이름표를 함께 씁니다.
"""
import math

from figlib import INK, SUB, text

OURS, IMU, UWB, SYM = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"   # 본 연구, IMU 단독, UWB 단독, 일반 융합
GRID = "#e3e6ea"
AXIS = "#9aa1ad"
TICK = 26        # 눈금 글자 크기
LAB = 27         # 축 이름·이름표 글자 크기


class Axes:
    def __init__(self, x0, y0, w, h, xlim, ylim, xlog=False, ylog=False):
        self.x0, self.y0, self.w, self.h = x0, y0, w, h
        self.xlim, self.ylim, self.xlog, self.ylog = xlim, ylim, xlog, ylog

    @staticmethod
    def _t(v, lim, log):
        if log:
            return (math.log10(v) - math.log10(lim[0])) / (math.log10(lim[1]) - math.log10(lim[0]))
        return (v - lim[0]) / (lim[1] - lim[0])

    def x(self, v):
        return self.x0 + self._t(v, self.xlim, self.xlog) * self.w

    def y(self, v):
        return self.y0 + self.h - self._t(v, self.ylim, self.ylog) * self.h

    def frame(self, xticks, yticks, xlabel=None, ylabel=None, xgrid=False):
        """xticks, yticks: [(값, 글)]. 가로 격자선과 아래 축선, 눈금 글, 축 이름."""
        out = []
        for v, _ in yticks:
            out.append(f'<line x1="{self.x0}" y1="{self.y(v):.1f}" x2="{self.x0 + self.w}" y2="{self.y(v):.1f}" '
                       f'stroke="{GRID}" stroke-width="2"/>')
        if xgrid:
            for v, _ in xticks:
                out.append(f'<line x1="{self.x(v):.1f}" y1="{self.y0}" x2="{self.x(v):.1f}" y2="{self.y0 + self.h}" '
                           f'stroke="{GRID}" stroke-width="2"/>')
        out.append(f'<line x1="{self.x0}" y1="{self.y0 + self.h}" x2="{self.x0 + self.w}" y2="{self.y0 + self.h}" '
                   f'stroke="{AXIS}" stroke-width="2.5"/>')
        for v, s in yticks:
            out.append(text(self.x0 - 14, self.y(v) + TICK * 0.35, s, TICK, 500, SUB, "end"))
        for v, s in xticks:
            out.append(f'<line x1="{self.x(v):.1f}" y1="{self.y0 + self.h}" x2="{self.x(v):.1f}" '
                       f'y2="{self.y0 + self.h + 10}" stroke="{AXIS}" stroke-width="2.5"/>')
            out.append(text(self.x(v), self.y0 + self.h + 14 + TICK, s, TICK, 500, SUB))
        if xlabel:
            out.append(text(self.x0 + self.w / 2, self.y0 + self.h + 54 + LAB, xlabel, LAB, 500, SUB))
        if ylabel:
            out.append(text(self.x0 - 14, self.y0 - 22, ylabel, LAB, 500, SUB, "end"))
        return "".join(out)

    def line(self, xs, ys, color, sw=5, dash=None, opacity=1.0):
        pts = " ".join(f"{self.x(a):.1f},{self.y(b):.1f}" for a, b in zip(xs, ys))
        d = f' stroke-dasharray="{dash}"' if dash else ""
        return (f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round" '
                f'stroke-linecap="round" opacity="{opacity}"{d}/>')

    def band(self, xs, lo, hi, color, opacity=0.14):
        up = " ".join(f"{self.x(a):.1f},{self.y(b):.1f}" for a, b in zip(xs, hi))
        dn = " ".join(f"{self.x(a):.1f},{self.y(b):.1f}" for a, b in zip(reversed(xs), reversed(lo)))
        return f'<polygon points="{up} {dn}" fill="{color}" opacity="{opacity}"/>'

    def dot(self, xv, yv, color, r=9):
        return f'<circle cx="{self.x(xv):.1f}" cy="{self.y(yv):.1f}" r="{r}" fill="{color}" stroke="#ffffff" stroke-width="4"/>'


def swatch(x, y, color, name, dash=None, kind="line"):
    """범례 한 줄: 선이나 네모 표본 + 이름(글은 글 색)."""
    if kind == "line":
        d = f' stroke-dasharray="{dash}"' if dash else ""
        mark = f'<line x1="{x}" y1="{y - 9}" x2="{x + 54}" y2="{y - 9}" stroke="{color}" stroke-width="6" stroke-linecap="round"{d}/>'
    else:
        mark = f'<rect x="{x + 10}" y="{y - 24}" width="30" height="30" rx="5" fill="{color}"/>'
    return mark + text(x + 68, y, name, LAB, 600, INK, "start")


def panel_title(x, y, s):
    return text(x, y, s, 29, 700, INK, "start")
