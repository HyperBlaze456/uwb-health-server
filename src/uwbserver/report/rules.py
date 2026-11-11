"""보고서와 대화의 순수 계산 (SPEC RP-4, RP-7, RP-8). DB·HTTP·모델 SDK를 모릅니다.

- build_context: 기간의 지표 값과 자료 범위 → 모델에 넘기는 맥락 `uwbserver.report_input/1`(RP-7). 값마다 v1, v2, …
- baseline_dates / baseline: 기간 앞 30일의 일별 값과 견주기(RP-4).
- format_value: 값을 글에 넣는 꼴. 규칙은 아래 "값의 글 꼴" 한 곳.
- check_text: 자리 표시({vN}, [pN])가 맥락과 이 대화의 찾은 대목을 가리키는지, 자리 표시 밖에 측정값을 숫자로 쓰지
  않았는지(RP-8 서버의 검사).
- render / Citations / references: 자리 표시를 값과 인용 번호로 바꾸고 문헌 목록을 만듦.
- parse_parts / check_report / assemble_report: 모델이 쓴 네 부분을 읽고 검사해 여덟 부분의 Markdown으로 엮음(RP-8 표).
"""

import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from uwbserver.pipeline import clock, metrics
from uwbserver.pipeline.metrics import Value

SCHEMA = "uwbserver.report_input/1"
SENSOR_POSITION = "lateral_ankle"  # 센서 위치(RP-7 coverage). 지금 키트는 하나뿐

# --- 서버가 정해 두는 글 (RP-8) ----------------------------------------------------------------

DISCLAIMER = "이 보고서는 진단이 아니며 참고용입니다."
LIMITATIONS = (
    "센서는 발목 바깥쪽에 차며, 이 위치에서 검증된 값은 보폭 속도와 그 95백분위뿐입니다. 나머지 보행 지표는 "
    "이 위치에서 검증되지 않았고, 자료가 적은 값은 표에 '자료 부족'으로 표시했습니다."
)
NOT_COMPARABLE_NOTE = (
    "기간이 하루보다 짧아 지난 30일의 일별 값과 조건이 다릅니다. 평소와의 차이는 참고로만 보십시오."
)
INSUFFICIENT = "자료 부족"
NO_VALUE = {"no_data": "값 없음", "needs_both_feet": "값 없음(양발 자료 필요)"}

# --- 보고서의 표 (RP-8의 4·5·7번 부분). 지표를 어느 표에 넣는지는 여기 한 곳 ---------------------------

ACTIVITY = (
    "period.walking_time_s", "period.bout_count", "period.bout_duration_max_s", "period.stride_count",
    "period.distance_m", "period.bout_duration_median_s", "period.bout_duration_p90_s",
    "period.walking_share_over_60s",
)  # fmt: skip
SPEED_STRIDE = (
    "period.speed_median_mps", "period.speed_p95_mps", "period.speed_10_30s_median_mps",
    "period.speed_30_60s_median_mps", "period.speed_over_60s_median_mps", "period.stride_length_median_m",
    "period.cadence_median_spm", "period.stride_time_median_s",
)  # fmt: skip
# 나머지 값(7번)은 위 둘에 없는 모든 값. 지표 목록에 새 지표가 생기면 여기에 넣기 전까지 나머지 값에 나옴.

# 모델이 쓰는 부분(2, 4, 5, 6번)의 제목. 모델은 이 제목으로 나눠 씀
SUMMARY, ACTIVITY_PART, SPEED_PART, OVERALL = "요약", "활동량", "속도와 보폭", "종합 풀이"
PARTS = (SUMMARY, ACTIVITY_PART, SPEED_PART, OVERALL)

BASELINE_DAYS = 30
BASELINE_MIN_DAYS = 7

# --- 맥락 (RP-7) ------------------------------------------------------------------------------


def metric_sources(catalog: metrics.Catalog) -> dict[str, tuple[str, ...]]:
    """지표 id → 근거 논문 key(search_papers의 metrics 범위, RP-6)."""
    return {m.id: m.sources for m in catalog.metrics}


def is_comparable(from_at: datetime, to_at: datetime) -> bool:
    """RP-4: 기간이 하루 이상이어야 일별 값과 그대로 견줄 수 있음."""
    return to_at - from_at >= timedelta(days=1)


def baseline_dates(from_at: datetime) -> tuple[date, date]:
    """RP-4: 견줄 일별 값의 날짜 범위(양 끝 포함). from_at이 든 한국 날짜는 기간과 겹치므로 그 앞날까지."""
    last = clock.local_date(from_at) - timedelta(days=1)
    return last - timedelta(days=BASELINE_DAYS - 1), last


def baseline(v: Value, rows: Iterable[Mapping]) -> dict | None:
    """RP-4: 같은 지표·발의 sufficient인 일별 값의 중앙값, 날의 수, 값과의 차이. 그런 날이 7일보다 적거나 값이 없으면 None.
    rows: summary.daily_value 행 {date, metric, side, value, sufficient}."""
    if v.value is None:
        return None
    xs = [r["value"] for r in rows if r["metric"] == v.metric and r["side"] == v.side and r["sufficient"]]
    if len(xs) < BASELINE_MIN_DAYS:
        return None
    median = statistics.median(xs)
    return {"median": median, "days": len(xs), "change": v.value - median}


def build_context(
    catalog: metrics.Catalog,
    *,
    subject: Mapping,
    from_at: datetime,
    to_at: datetime,
    values: Sequence[Value],
    daily_rows: Sequence[Mapping],
    coverage: Mapping,
    runs: Sequence[int],
) -> dict:
    """맥락 `uwbserver.report_input/1`(RP-7). JSON으로 그대로 옮길 수 있는 dict.

    - subject: {code, height_m, leg_length_m, paretic_side}. 실명은 없음.
    - values: metrics.period_values의 결과(지표 목록 순서). 값이 없는 것(reason)도 넣음. 번호는 v1부터 이 순서.
    - daily_rows: 기간 앞 30일(baseline_dates)의 일별 값.
    - coverage: {wear_time_s, feet, partial_runs, ambiguous_bouts, runs_not_unpacked}. 걷기 시간·걷기 구간 수·UWB 융합
      비율은 값에서 가져옴(같은 숫자가 두 곳에서 다르게 나오지 않게).
    """
    out_values = []
    for i, v in enumerate(values, 1):
        m = catalog.metric(v.metric)
        d = {"id": f"v{i}", **v.as_dict()}
        if m is not None:
            d |= {"label": m.label, "meaning": m.meaning, "show": m.show, "sources": list(m.sources)}
        if (b := baseline(v, daily_rows)) is not None:
            d["baseline"] = b
        out_values.append(d)

    def first(metric_id: str):
        return next((v.value for v in values if v.metric == metric_id), None)

    return {
        "schema": SCHEMA,
        "catalog_version": catalog.version,
        "subject": {k: subject.get(k) for k in ("code", "height_m", "leg_length_m", "paretic_side")},
        "period": {"from_at": from_at.isoformat(), "to_at": to_at.isoformat()},
        "baseline_comparable": is_comparable(from_at, to_at),
        "coverage": {
            "wear_time_s": coverage["wear_time_s"],
            "walking_time_s": first("period.walking_time_s"),
            "bout_count": first("period.bout_count"),
            "feet": list(coverage["feet"]),
            "sensor_position": SENSOR_POSITION,
            "fused_ratio": first("period.fused_ratio"),
            "partial_runs": coverage["partial_runs"],
            "ambiguous_bouts": coverage["ambiguous_bouts"],
            "runs_not_unpacked": coverage["runs_not_unpacked"],
        },
        "values": out_values,
        "runs": list(runs),
    }


def values_by_number(context: Mapping) -> dict[int, Mapping]:
    return {int(v["id"][1:]): v for v in context["values"]}


# --- 값의 글 꼴 ----------------------------------------------------------------------------------
# 규칙: 유효 숫자 3자리(1000 이상은 정수로 반올림해 천 단위 쉼표). 비율(ratio)은 백분율, 개수(count)는 단위 없는 정수,
# 초(s)는 60초 미만은 초, 1시간 미만은 분, 그 위는 시간. 단위 이름은 아래 표. 표에 없는 단위는 이름 그대로 붙임.

_UNIT_TEXT = {"m": " m", "m/s": " m/s", "steps/min": "걸음/분", "deg": "°", "deg/s": "°/s", "Hz": " Hz", "g": " g"}


def _sig3(x: float) -> str:
    if x == 0:
        return "0"
    if abs(x) >= 1000:
        return f"{round(x):,}"
    text = f"{x:#.3g}"
    return text[:-1] if text.endswith(".") else text


def format_value(x: float | int, unit: str) -> str:
    if unit == "count":
        return f"{round(x):,}"
    if unit == "ratio":
        return f"{_sig3(x * 100)}%"
    if unit == "s":
        if abs(x) < 60:
            return f"{_sig3(x)}초"
        if abs(x) < 3600:
            return f"{_sig3(x / 60)}분"
        return f"{_sig3(x / 3600)}시간"
    return _sig3(x) + _UNIT_TEXT.get(unit, f" {unit}")


def format_change(x: float, unit: str) -> str:
    """차이(baseline.change). 부호를 붙이고, 비율은 백분율포인트(%p)."""
    sign = "+" if x >= 0 else "-"
    if unit == "ratio":
        return f"{sign}{_sig3(abs(x) * 100)}%p"
    return sign + format_value(abs(x), unit)


def value_text(v: Mapping) -> str:
    """맥락의 값 하나 → 글에 넣는 꼴. 값이 없으면 이유를 짧게."""
    if v["value"] is None:
        return NO_VALUE.get(v.get("reason"), "값 없음")
    return format_value(v["value"], v["unit"])


# --- 자리 표시와 검사 (RP-8) ----------------------------------------------------------------------

V_RE = re.compile(r"\{v(\d+)\}")
P_RE = re.compile(r"\[p(\d+)\]")
# 측정값으로 보는 숫자: 숫자 바로 뒤(빈칸 하나까지)에 지표 목록의 단위나 그 한국어 이름, %가 옴.
_UNITS = (r"%p|%|퍼센트|m/s|km/h|cm|mm|미터|센티미터|m(?![A-Za-z])|sec(?![A-Za-z])|s(?![A-Za-z])|초|분(?!의)|"
          r"시간|걸음/분|steps/min|spm|걸음|°|deg(?![A-Za-z])|Hz|g(?![A-Za-z])|회")  # fmt: skip
_MEASURED = re.compile(rf"(?<![\d.,A-Za-z_])(\d+(?:[.,]\d+)*)\s?({_UNITS})")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?!\[p\d)|\n+")
_SECONDS = {"초", "s", "sec"}


@dataclass(frozen=True)
class PassageRef:
    """이 대화에서 search_papers가 돌려준 대목 하나와 그 논문의 서지. number는 pN의 N."""

    number: int
    passage_id: str
    key: str
    page: int | None
    text: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    doi: str | None


def _thresholds(context: Mapping) -> set[float]:
    """where의 문턱 값(초). '30초 이상 걷기'는 정의이지 측정값이 아님."""
    out = set()
    for v in context["values"]:
        for key in ("bout_min_s", "bout_max_s"):
            x = (v.get("where") or {}).get(key)
            if isinstance(x, int | float):
                out.add(float(x))
    return out


def measured_numbers(text: str, context: Mapping) -> list[str]:
    """자리 표시 밖에 숫자로 쓴 측정값으로 보이는 것(RP-8).

    규칙: 숫자 바로 뒤에 단위(_UNITS)가 오면 측정값으로 봄. 다음은 뺌: 자리 표시 자체, 맥락 지표의 label 안의 숫자,
    where 문턱 값의 초(예 '30초 이상'), 인용 [pN]이 있는 문장의 숫자(문헌이 보고한 값).
    알려진 빈틈: 단위 없이 쓴 측정값('속도는 1.1이었다'), 글로 쓴 수, 표에 없는 단위는 못 잡음. 인용한 문장 안에 측정값을
    숫자로 쓰면 못 잡음. 인용 없이 쓴 문헌의 수치는 잡음(인용을 붙이라는 뜻이 됨).
    """
    labels = sorted({v.get("label", "") for v in context["values"]} - {""}, key=len, reverse=True)
    thresholds = _thresholds(context)
    out = []
    for sentence in _SENTENCE_END.split(text):
        if P_RE.search(sentence):
            continue
        s = V_RE.sub(" ", sentence)
        for label in labels:
            s = s.replace(label, " ")
        for m in _MEASURED.finditer(s):
            number, unit = m.group(1), m.group(2)
            try:
                x = float(number.replace(",", ""))
            except ValueError:
                x = math.nan
            if unit in _SECONDS and x in thresholds:
                continue
            out.append(m.group(0))
    return out


def check_text(text: str, context: Mapping, passages: Mapping[int, PassageRef]) -> list[str]:
    """RP-8 서버의 검사(글 하나). 어긴 것마다 한 줄, 없으면 빈 목록.
    passages: 이 대화에서 지금까지 돌려준 대목(번호 → 대목)."""
    known = values_by_number(context)
    problems = []
    for n in dict.fromkeys(int(x) for x in V_RE.findall(text)):
        if n not in known:
            problems.append(f"맥락에 없는 값 번호 {{v{n}}}")
    for n in dict.fromkeys(int(x) for x in P_RE.findall(text)):
        if n not in passages:
            problems.append(f"이 대화에서 search_papers가 돌려주지 않은 대목 [p{n}]")
    for found in measured_numbers(text, context):
        problems.append(f"측정값을 숫자로 직접 씀: '{found}'. 자리 표시 {{vN}}를 써야 함")
    return problems


@dataclass
class Citations:
    """인용 번호: 처음 쓴 순서대로 1, 2, … (같은 대목은 같은 번호)."""

    order: list[int] = field(default_factory=list)

    def number(self, p: int) -> int:
        if p not in self.order:
            self.order.append(p)
        return self.order.index(p) + 1


def render(text: str, context: Mapping, passages: Mapping[int, PassageRef], citations: Citations) -> str:
    """{vN} → 값과 단위, [pN] → [인용 번호]. 모르는 번호는 그대로 둠(check_text가 잡음)."""
    known = values_by_number(context)

    def value(m: re.Match) -> str:
        v = known.get(int(m.group(1)))
        return m.group(0) if v is None else value_text(v)

    def cite(m: re.Match) -> str:
        n = int(m.group(1))
        return m.group(0) if n not in passages else f"[{citations.number(n)}]"

    return P_RE.sub(cite, V_RE.sub(value, text))


def references(citations: Citations, passages: Mapping[int, PassageRef]) -> str:
    """인용 번호 순서의 문헌 목록(Markdown 번호 목록). 줄마다 저자, 연도, 제목, doi, 대목 id."""
    lines = []
    for i, p in enumerate(citations.order, 1):
        ref = passages[p]
        authors = ", ".join(ref.authors) or "저자 없음"
        year = ref.year if ref.year is not None else "연도 없음"
        doi = f" doi:{ref.doi}." if ref.doi else ""
        page = f", {ref.page}쪽" if ref.page is not None else ""
        lines.append(f"{i}. {authors} ({year}). {ref.title}.{doi} 대목 {ref.passage_id}{page}")
    return "\n".join(lines)


# --- 보고서 (RP-8) ----------------------------------------------------------------------------------

_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def parse_parts(text: str) -> tuple[dict[str, str], list[str]]:
    """모델이 쓴 보고서 글 → {부분 이름: 글}(PARTS 순서), 문제 목록. 부분은 '## 이름' 줄로 나눔.
    첫 제목 앞의 글은 버림. 모르는 제목, 두 번 나온 제목, 빠지거나 빈 부분이 문제."""
    found: dict[str, list[str]] = {}
    problems = []
    current: list[str] | None = None
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            name = m.group(1)
            if name not in PARTS:
                problems.append(f"정해진 부분이 아닌 제목 '## {name}'")
                current = None
            elif name in found:
                problems.append(f"부분 '{name}'이 두 번 나옴")
                current = None
            else:
                current = found[name] = []
            continue
        if current is not None:
            current.append(line)
    parts = {}
    for name in PARTS:
        if name not in found:
            problems.append(f"부분 '## {name}'이 없음")
            continue
        body = "\n".join(found[name]).strip()
        if not body:
            problems.append(f"부분 '{name}'이 비었음")
        parts[name] = body
    return parts, problems


def check_report(parts: Mapping[str, str], context: Mapping, passages: Mapping[int, PassageRef]) -> list[str]:
    """부분마다 check_text, 그리고 요약은 show = basic인 값만(RP-8 표의 2번)."""
    known = values_by_number(context)
    problems = []
    for name, text in parts.items():
        problems += [f"{name}: {p}" for p in check_text(text, context, passages)]
    for n in dict.fromkeys(int(x) for x in V_RE.findall(parts.get(SUMMARY, ""))):
        if n in known and known[n].get("show") != "basic":
            problems.append(f"{SUMMARY}: show = basic이 아닌 값 {{v{n}}}을 씀(요약은 basic 값만)")
    return problems


def correction_request(problems: Sequence[str]) -> str:
    """검사에 걸린 보고서를 한 번 다시 쓰게 하는 말(RP-8)."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "서버 검사에서 다음 문제가 나왔습니다.\n"
        f"{listed}\n"
        "문제를 고쳐 같은 네 제목(## 요약, ## 활동량, ## 속도와 보폭, ## 종합 풀이)으로 보고서 전체를 다시 써 주세요."
    )


_SIDE = {"left": "왼발", "right": "오른발", None: "—"}


def _table(values: Sequence[Mapping]) -> str:
    if not values:
        return "값 없음."
    rows = ["| 번호 | 지표 | 발 | 값 | 표본 수 | 지난 30일 중앙값(일수) | 차이 | 비고 |",
            "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for v in values:
        b = v.get("baseline")
        base = f"{format_value(b['median'], v['unit'])} ({b['days']}일)" if b else "—"
        change = format_change(b["change"], v["unit"]) if b else "—"
        note = INSUFFICIENT if v["value"] is not None and not v["sufficient"] else ""
        rows.append(f"| {v['id']} | {v.get('label', v['metric'])} | {_SIDE.get(v['side'], v['side'])} | "
                    f"{value_text(v)} | {v['n']} | {base} | {change} | {note} |")  # fmt: skip
    return "\n".join(rows)


def _local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(clock.SEOUL).strftime("%Y-%m-%d %H:%M")


def _coverage(context: Mapping) -> str:
    c = context["coverage"]

    def opt(x, unit: str) -> str:
        return "값 없음" if x is None else format_value(x, unit)

    feet = ", ".join(_SIDE.get(f, f) for f in c["feet"]) or "없음"
    lines = [
        f"- 자료가 있던 시간: {opt(c['wear_time_s'], 's')}",
        f"- 걷기 시간: {opt(c['walking_time_s'], 's')}, 걷기 구간: {opt(c['bout_count'], 'count')}개",
        f"- 찬 발: {feet}. 센서 위치: 발목 바깥쪽({c['sensor_position']})",
        f"- UWB 융합 비율: {opt(c['fused_ratio'], 'ratio')}",
        f"- 빠진 자료: 일부만 분석된 run {c['partial_runs']}개, 주인이 모호해 뺀 걷기 구간 {c['ambiguous_bouts']}개, "
        f"아직 풀리지 않은 run {c['runs_not_unpacked']}개",
    ]
    if not context["baseline_comparable"]:
        lines.append("")
        lines.append(NOT_COMPARABLE_NOTE)
    return "\n".join(lines)


def assemble_report(
    context: Mapping, parts: Mapping[str, str], passages: Mapping[int, PassageRef], *, versions: Mapping[str, str]
) -> str:
    """여덟 부분의 보고서(RP-8 표). 1·3·7·8번과 4·5번의 표는 맥락에서, 2·4·5·6번의 글은 모델의 parts에서.
    versions: {papers, prompt, model}. 인용 번호는 글에 처음 나온 순서(2 → 4 → 5 → 6번)."""
    cites = Citations()
    text = {name: render(parts.get(name, ""), context, passages, cites) for name in PARTS}
    values = context["values"]
    activity = [v for v in values if v["metric"] in ACTIVITY]
    speed = [v for v in values if v["metric"] in SPEED_STRIDE]
    rest = [v for v in values if v["metric"] not in ACTIVITY and v["metric"] not in SPEED_STRIDE]
    period = context["period"]
    head = "\n".join([
        "# 보행 보고서",
        "",
        f"- 착용자: {context['subject']['code']}",
        f"- 기간: {_local(period['from_at'])} – {_local(period['to_at'])} (한국 시각)",
        f"- 자료의 판: 지표 목록 {context['catalog_version']}, 논문 목록 {versions.get('papers', '')[:12]}, "
        f"글 지시 {versions.get('prompt', '')}, 모델 {versions.get('model', '')}",
        "",
        f"> {DISCLAIMER}",
    ])  # fmt: skip
    refs = references(cites, passages) or "인용한 문헌이 없습니다."
    sections = [
        head,
        f"## {SUMMARY}\n\n{text[SUMMARY]}",
        f"## 자료 범위\n\n{_coverage(context)}",
        f"## {ACTIVITY_PART}\n\n{_table(activity)}\n\n{text[ACTIVITY_PART]}",
        f"## {SPEED_PART}\n\n{_table(speed)}\n\n{text[SPEED_PART]}",
        f"## {OVERALL}\n\n{text[OVERALL]}",
        f"## 나머지 값\n\n{_table(rest)}",
        f"## 한계와 근거 문헌\n\n{LIMITATIONS}\n\n{refs}",
    ]
    return "\n\n".join(s.rstrip() for s in sections) + "\n"
