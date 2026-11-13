"""그림: 실제 모델이 쓴 리포트의 한 부분. 서버가 넣은 값과 검색된 단락의 인용을 표시합니다.

    uv run --with pillow python report/figures/src/fig_report.py <run id>     (서버 저장소의 환경에서)
"""
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

from uwbserver.report import rules

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
RUNS = HERE.parents[1] / "data" / "rag_runs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

run = json.loads((RUNS / f"{sys.argv[1]}.json").read_text())
trace = run["trace"]
ctx = rules.fill_directions(trace["context"]) if hasattr(rules, "fill_directions") else trace["context"]
raw = next(e["raw"] for e in reversed(trace["events"]) if e["type"] == "report")
parts, _ = rules.parse_parts(raw)
values = rules.values_by_number(ctx)
order: list[int] = []


def mark(text: str) -> str:
    """원문(자리 표시 포함) → HTML. 값은 파란 칩, 인용은 주황 칩."""
    out = html.escape(text)

    def val(m):
        v = values.get(int(m.group(1)))
        return m.group(0) if v is None else f'<span class="v">{html.escape(rules.value_text(v))}</span>'

    def cite(m):
        n = int(m.group(1))
        if n not in order:
            order.append(n)
        return f'<span class="c">{order.index(n) + 1}</span>'

    return rules.P_RE.sub(cite, rules.V_RE.sub(val, out))


def sentences(text: str, n: int) -> str:
    flat = " ".join(text.split())
    return " ".join(re.split(r"(?<=[.!?다])\s+(?=[^\[])", flat)[:n])


for name in rules.PARTS[:3]:
    mark(parts[name])                       # 인용 번호는 글 전체에서 처음 나온 순서
summary = mark(sentences(parts[rules.SUMMARY], 4))
# 종합 풀이: 인용이 가장 많은 문단을 보임(Markdown 표시는 뗌)
paras = [re.sub(r"\*\*|^[-•]\s*", "", x.strip(), flags=re.M) for x in re.split(r"\n\s*\n", parts[rules.OVERALL]) if x.strip()]
best = max(paras, key=lambda x: len(rules.P_RE.findall(x)))
overall = mark(sentences(best, int(sys.argv[2]) if len(sys.argv) > 2 else 4))
rows = ""
for v in ctx["values"]:
    if v["metric"] in ("period.walking_time_s", "period.bout_count", "period.speed_median_mps", "period.stride_time_cv"):
        b = v.get("baseline")
        base = rules.format_value(b["median"], v["unit"]) if b else "—"
        change = rules.format_change(b["change"], v["unit"]) if b else "—"
        rows += (f"<tr><td>{html.escape(v.get('label', v['metric']))}</td><td>{html.escape(rules.value_text(v))}</td>"
                 f"<td>{html.escape(base)}</td><td>{html.escape(change)}</td></tr>")
period = ctx["period"]["from_at"][:10]
page = f"""<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;background:#fff}}
body{{font-family:'Apple SD Gothic Neo',sans-serif;color:#111827;width:1740px}}
.card{{margin:6px;border:3px solid #c5cad3;border-radius:18px;padding:26px 34px 30px;display:grid;grid-template-columns:1fr 1fr;gap:10px 44px}}
.head{{grid-column:1/3;display:flex;justify-content:space-between;align-items:baseline;border-bottom:3px solid #e3e6ea;padding-bottom:14px}}
.head b{{font-size:31px}} .head span{{font-size:25px;color:#4b5563}}
h3{{font-size:28px;margin:14px 0 8px}} p{{font-size:25.5px;line-height:1.58;margin:0;text-align:justify;word-break:keep-all}}
.v{{background:#e6edff;color:#1d4ed8;font-weight:700;border-radius:7px;padding:1px 7px;white-space:nowrap}}
.c{{background:#fdeee4;color:#c2410c;font-weight:700;border-radius:7px;padding:1px 9px;font-size:22px;margin-left:3px}}
table{{border-collapse:collapse;width:100%;font-size:24.5px;margin-top:6px}}
th,td{{border-bottom:2px solid #e3e6ea;padding:7px 8px;text-align:right}} th:first-child,td:first-child{{text-align:left}}
th{{color:#4b5563;font-weight:600;border-bottom:3px solid #9aa1ad}}
.legend{{font-size:24px;color:#4b5563}} .legend .v,.legend .c{{margin:0 6px 0 16px}}
</style><div class="card">
<div class="head"><b>보행 보고서 · 착용자 S-DEMO · {period}</b>
<span class="legend"><span class="v">값</span>서버가 넣은 값<span class="c">n</span>검색된 단락의 인용</span></div>
<div><h3>요약</h3><p>{summary}</p>
<h3>값의 표(서버가 작성)</h3><table><tr><th>지표</th><th>값</th><th>지난 30일 중앙값</th><th>차이</th></tr>{rows}</table></div>
<div><h3>종합 풀이</h3><p>{overall}</p></div>
</div>"""
with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "r.html"
    f.write_text(page, encoding="utf-8")
    png = OUT / "fig_report.png"
    png.unlink(missing_ok=True)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--user-data-dir={Path(tempfile.gettempdir()) / 're-report-chrome-profile'}", "--window-size=1740,1500",
                    "--force-device-scale-factor=2", f"--screenshot={png}", f.as_uri()], capture_output=True, timeout=180)
im = Image.open(png).convert("RGB")
box = ImageChops.difference(im, Image.new("RGB", im.size, "#ffffff")).getbbox()
im.crop((0, 0, im.width, box[3] + 8)).save(png)
print(png, im.width, box[3] + 8)
