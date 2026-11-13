"""R&E 보고서 그림의 공통 부품: 아이콘 넣기, 상자, 직각 화살표, HTML로 감싸 Chrome으로 PNG 만들기."""
import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "icons"
OUT = HERE.parent / "out"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

INK = "#111827"
SUB = "#4b5563"
LINE = "#374151"
GROUP_FILL = "#f3f4f6"
GROUP_STROKE = "#c5cad3"
ACCENT = "#1d4ed8"
ACCENT_FILL = "#e6edff"
WARN = "#c2410c"
FONT = "'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"


def icon(name, cx, cy, size=60, color=INK, sw=1.6):
    """Tabler 아이콘(24×24)의 path를 (cx, cy) 가운데에 size 크기로."""
    src = (ICONS / f"{name}.svg").read_text()
    paths = re.findall(r"<path[^>]*/>", src)
    body = "".join(p for p in paths if 'stroke="none"' not in p)
    s = size / 24
    return (f'<g transform="translate({cx - size / 2:.1f},{cy - size / 2:.1f}) scale({s:.4f})" fill="none" '
            f'stroke="{color}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def text(x, y, s, size=28, weight=500, color=INK, anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
            f'text-anchor="{anchor}">{s}</text>')


def node(cx, cy, label, icon_name, w=146, h=150, accent=False, icon_size=58, size=29):
    fill, stroke, sw = (ACCENT_FILL, ACCENT, 3) if accent else ("#ffffff", LINE, 2)
    col = ACCENT if accent else INK
    lines = label.split("\n")
    top = cy - h / 2
    out = [f'<rect x="{cx - w / 2}" y="{top}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" '
           f'stroke-width="{sw}"/>']
    iy = top + 22 + icon_size / 2 if len(lines) == 1 else top + 16 + icon_size / 2
    out.append(icon(icon_name, cx, iy, icon_size, col))
    ty = iy + icon_size / 2 + size + 6
    for i, ln in enumerate(lines):
        out.append(text(cx, ty + i * (size + 5), ln, size, 600, INK))
    return "".join(out)


def group(x, y, w, h, title, title_size=27):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{GROUP_FILL}" stroke="{GROUP_STROKE}" '
            f'stroke-width="2"/>' + text(x + 22, y + 40, title, title_size, 700, SUB, "start"))


def arrow(points, color=LINE, sw=3, both=False, dash=None):
    """points: [(x, y), ...] 직각으로 꺾이는 선. 끝에 화살촉."""
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    mid = "a" if color == LINE else ("b" if color == ACCENT else "c")
    start = f' marker-start="url(#as{mid})"' if both else ""
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{extra} '
            f'marker-end="url(#ae{mid})"{start}/>')


def label(x, y, s, size=25, color=SUB, anchor="middle", bg="#ffffff", weight=500):
    """화살표 이름표: 글 뒤에 바탕색 띠."""
    w = sum(1.0 if ord(c) > 0x2000 else 0.58 for c in s) * size + 14
    x0 = x - w / 2 if anchor == "middle" else (x - 7 if anchor == "start" else x - w + 7)
    rect = f'<rect x="{x0:.1f}" y="{y - size + 2}" width="{w:.1f}" height="{size + 8}" rx="6" fill="{bg}"/>' if bg else ""
    return rect + text(x, y, s, size, weight, color, anchor)


def defs():
    out = ["<defs>"]
    for mid, color in (("a", LINE), ("b", ACCENT), ("c", WARN)):
        out.append(f'<marker id="ae{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5.5" markerHeight="5.5" '
                   f'orient="auto-start-reverse"><path d="M0,0.6 L10,5 L0,9.4 z" fill="{color}"/></marker>')
        out.append(f'<marker id="as{mid}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5.5" markerHeight="5.5" '
                   f'orient="auto-start-reverse"><path d="M0,0.6 L10,5 L0,9.4 z" fill="{color}"/></marker>')
    out.append("</defs>")
    return "".join(out)


def render(name, w, h, body, scale=2):
    """SVG를 out/<name>.svg와 out/<name>.png로."""
    OUT.mkdir(exist_ok=True)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           f'font-family="{FONT}">{defs()}<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>')
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    html = OUT / f"{name}.html"
    html.write_text(f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;background:#fff}}svg{{display:block}}'
                    f'</style>{svg}', encoding="utf-8")
    png = OUT / f"{name}.png"
    png.unlink(missing_ok=True)
    profile = Path(tempfile.gettempdir()) / "re-report-chrome-profile"
    # Chrome은 그림을 쓴 뒤 끝낼 때 0이 아닌 코드를 낼 때가 있어, 파일이 생겼는지로 성공을 판단합니다.
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                    f"--window-size={w},{h}", f"--force-device-scale-factor={scale}", f"--screenshot={png}",
                    html.as_uri()], capture_output=True, timeout=180)
    html.unlink()
    if not png.exists():
        raise RuntimeError(f"Chrome did not write {png}")
    return png
