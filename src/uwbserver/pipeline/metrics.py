"""C4: 지표 목록(contracts/metrics.toml) 읽기와 줄의 풀이 (SPEC 14.4 MC, 14.5 FT-3·FT-4).

분석 값의 이름·단위·뜻은 지표 목록 하나가 정합니다(MC-1). 서버는 gaitfusion이 낸 줄을 그대로 저장하고,
풀이(이름 옮기기, 단위 환산, 형과 범위 검사)는 읽을 때 여기서 합니다(MC-4). 그래서 gaitfusion의 출력이 바뀌어도
서버 코드와 표는 그대로이고, 지표 목록 파일만 고칩니다.

DB·HTTP를 모르는 순수 코드입니다. 파일은 지표 목록을 읽기만 합니다.

- load / parse: 지표 목록 파일 → Catalog. 키가 빠지거나 형이 틀린 항목은 CatalogError.
- check: 목록 자체의 검사(MC-7 1번). 어긴 것마다 한 줄씩 돌려줌.
- formats_of: 응답의 summary.formats → 표마다 형식(MC-3).
- normalize: 저장된 줄 하나 → {지표 id: 값}(FT-4).
- inspect: 줄 하나에서 틀린 값의 수와 목록에 없는 필드 이름(FT-3).
- period_values: 고른 걷기 구간과 그 줄 → period 지표마다 값(RP-3). 시스템의 수치 계산은 모두 여기서 함.
- bouts_from_rows: 걷기 구간과 줄의 DB 행 → period_values의 입력(SM-3, RP-2).
"""

import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

# --- 정해진 이름 (MC-1, MC-2, RP-3) --------------------------------------------------

TYPES = frozenset({"number", "integer", "text"})
PRODUCERS = frozenset({"gaitfusion", "server"})
SHOW = frozenset({"basic", "detail"})
NEEDS = frozenset({"one_foot", "both_feet"})
TRUST = frozenset({"validated", "proxy", "low", "unverified", "fact"})
RULES = frozenset({"given", "count", "sum", "max", "median", "p90", "p95", "cv", "sd", "ratio", "cadence"})
WHERE_KEYS = frozenset({"bout_min_s", "bout_max_s", "mode"})
MODES = frozenset({"fused", "imu_only"})
BOUT_DURATION = "bout.duration_s"  # 서버가 아는 값: 걷기 구간의 길이(MC-1 from)
PERIOD = "period"

# 이름 끝 단위 접미 → unit(MC-2). 접미가 없으면 비율(0–1) 또는 개수.
UNIT_SUFFIX = {
    "m": "m", "s": "s", "mps": "m/s", "deg": "deg", "dps": "deg/s", "hz": "Hz", "g": "g", "spm": "steps/min",
}  # fmt: skip
UNITLESS = frozenset({"ratio", "count"})

ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_]*$")
SOURCE_RE = re.compile(r"^[a-z]+[0-9]{4}[a-z]?$")  # <첫 저자 성 소문자><연도>, 같은 저자·연도는 a, b
FORMAT_RE = re.compile(r"^gaitfusion\.(?P<level>[a-z]+)/(?P<n>[1-9][0-9]*)$")
# 7절의 공통 키(줄마다 시작·끝·발). 이름은 [[table]].keys가 정함
KEY_ROLES = ("start", "end", "side")

_TABLE_KEYS = {"field", "level", "formats", "keys"}
_METRIC_REQUIRED = {"id", "level", "type", "unit", "producer", "show", "needs", "trust", "label", "meaning", "sources"}
_METRIC_OPTIONAL = {"range", "names", "from", "rule", "where", "by_side", "min_n"}


class CatalogError(ValueError):
    """지표 목록 파일의 꼴이 틀림(키가 빠짐, 모르는 키, 형이 틀림)."""


@dataclass(frozen=True)
class Table:
    """[[table]]: 응답 bouts[] 안의 배열 하나 = 줄의 종류 하나."""

    field: str  # 응답의 배열 이름(예 strides)
    level: str  # 줄의 종류(예 stride)
    formats: tuple[str, ...]  # 서버가 풀이할 수 있는 판
    keys: Mapping[str, str]  # {"start": ..., "end": ..., "side": ...} → 줄의 필드 이름


@dataclass(frozen=True)
class Metric:
    """[[metric]] 하나(MC-1의 키). period 지표만 from_·rule·where·by_side·min_n이 있음."""

    id: str
    level: str
    type: str
    unit: str
    range: tuple[float, float] | None
    producer: str
    show: str
    needs: str
    trust: str
    label: str
    meaning: str
    sources: tuple[str, ...]
    names: Mapping[str, tuple[str, float]] = field(default_factory=dict)  # 형식 → (필드 이름, 배수)
    from_: str | None = None
    rule: str | None = None
    where: Mapping[str, object] = field(default_factory=dict)
    by_side: bool | None = None
    min_n: int | None = None

    @property
    def name(self) -> str:
        """id의 이름 부분(stride.length_m → length_m)."""
        return self.id.split(".", 1)[1]

    def field_in(self, fmt: str) -> tuple[str, float]:
        """이 형식의 줄에서 이 지표의 (필드 이름, 단위 환산 배수). names에 없으면 (이름 부분, 1)."""
        return self.names.get(fmt, (self.name, 1.0))


@dataclass(frozen=True)
class Catalog:
    version: int
    tables: tuple[Table, ...]
    metrics: tuple[Metric, ...]

    @cached_property
    def _by_id(self) -> dict[str, Metric]:
        return {m.id: m for m in self.metrics}

    def table_by_field(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.field == name), None)

    def table_by_level(self, level: str) -> Table | None:
        return next((t for t in self.tables if t.level == level), None)

    def metric(self, metric_id: str) -> Metric | None:
        return self._by_id.get(metric_id)

    def metrics_of(self, level: str) -> tuple[Metric, ...]:
        return tuple(m for m in self.metrics if m.level == level)


# --- 읽기 ---------------------------------------------------------------------------


def default_path() -> Path:
    """저장소 뿌리/contracts/metrics.toml. 이미지에서는 /app/contracts/metrics.toml(migrate.py와 같은 찾기)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contracts" / "metrics.toml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("contracts/metrics.toml을 찾지 못함")


def load(path: Path | str | None = None) -> Catalog:
    import tomllib

    with open(path or default_path(), "rb") as f:
        return parse(tomllib.load(f))


def _need(cond: bool, where: str, what: str) -> None:
    if not cond:
        raise CatalogError(f"{where}: {what}")


def _is_num(x: object) -> bool:
    return type(x) in (int, float)


def _str(entry: Mapping, key: str, where: str) -> str:
    v = entry[key]
    _need(isinstance(v, str) and v.strip() != "", where, f"{key}는 비지 않은 글자여야 함")
    return v


def _one_of(entry: Mapping, key: str, allowed: frozenset[str], where: str) -> str:
    v = _str(entry, key, where)
    _need(v in allowed, where, f"{key} = {v!r}, 가능한 것 {sorted(allowed)}")
    return v


def _table(entry: object, k: int) -> Table:
    where = f"table[{k}]"
    _need(isinstance(entry, dict), where, "표(키와 값)여야 함")
    where = f"table {entry.get('field', k)}"
    _need(entry.keys() == _TABLE_KEYS, where, f"키는 정확히 {sorted(_TABLE_KEYS)}")
    formats = entry["formats"]
    _need(isinstance(formats, list) and formats and all(isinstance(f, str) for f in formats), where,
          "formats는 글자의 목록")  # fmt: skip
    keys = entry["keys"]
    _need(isinstance(keys, dict) and set(keys) == set(KEY_ROLES) and all(isinstance(v, str) for v in keys.values()),
          where, f"keys는 {list(KEY_ROLES)} → 필드 이름")  # fmt: skip
    return Table(_str(entry, "field", where), _str(entry, "level", where), tuple(formats), dict(keys))


def _names(raw: object, where: str) -> dict[str, tuple[str, float]]:
    _need(isinstance(raw, dict), where, "names는 {형식 = {name, scale}}")
    out = {}
    for fmt, spec in raw.items():
        _need(isinstance(spec, dict) and set(spec) <= {"name", "scale"} and isinstance(spec.get("name"), str),
              where, f"names.{fmt}는 {{name = 글자, scale = 수}}")  # fmt: skip
        scale = spec.get("scale", 1.0)
        _need(_is_num(scale) and math.isfinite(scale) and scale != 0, where, f"names.{fmt}.scale은 0이 아닌 유한한 수")
        out[fmt] = (spec["name"], float(scale))
    return out


def _metric(entry: object, k: int) -> Metric:
    where = f"metric[{k}]"
    _need(isinstance(entry, dict), where, "표(키와 값)여야 함")
    where = f"metric {entry.get('id', k)}"
    missing = _METRIC_REQUIRED - entry.keys()
    _need(not missing, where, f"빠진 키 {sorted(missing)}")
    extra = entry.keys() - _METRIC_REQUIRED - _METRIC_OPTIONAL
    _need(not extra, where, f"모르는 키 {sorted(extra)}")

    rng = entry.get("range")
    if rng is not None:
        _need(isinstance(rng, list) and len(rng) == 2 and all(_is_num(x) for x in rng) and rng[0] <= rng[1], where,
              "range는 [아래, 위] 두 수(아래 ≤ 위)")  # fmt: skip
        rng = (float(rng[0]), float(rng[1]))
    sources = entry["sources"]
    _need(isinstance(sources, list) and all(isinstance(s, str) for s in sources), where, "sources는 글자의 목록")
    by_side, min_n, where_ = entry.get("by_side"), entry.get("min_n"), entry.get("where", {})
    _need(by_side is None or isinstance(by_side, bool), where, "by_side는 true/false")
    _need(min_n is None or (type(min_n) is int and min_n >= 1), where, "min_n은 1 이상의 정수")
    _need(isinstance(where_, dict), where, "where는 {이름 = 값}")
    for key in ("from", "rule"):
        _need(entry.get(key) is None or isinstance(entry[key], str), where, f"{key}는 글자")

    return Metric(
        id=_str(entry, "id", where),
        level=_str(entry, "level", where),
        type=_one_of(entry, "type", TYPES, where),
        unit=_str(entry, "unit", where),
        range=rng,
        producer=_one_of(entry, "producer", PRODUCERS, where),
        show=_one_of(entry, "show", SHOW, where),
        needs=_one_of(entry, "needs", NEEDS, where),
        trust=_one_of(entry, "trust", TRUST, where),
        label=_str(entry, "label", where),
        meaning=_str(entry, "meaning", where),
        sources=tuple(sources),
        names=_names(entry["names"], where) if "names" in entry else {},
        from_=entry.get("from"),
        rule=entry.get("rule"),
        where=dict(where_),
        by_side=by_side,
        min_n=min_n,
    )


def parse(doc: Mapping) -> Catalog:
    """tomllib로 읽은 지표 목록 → Catalog. 꼴이 틀리면 그 항목을 밝힌 CatalogError."""
    version = doc.get("version")
    _need(type(version) is int and version >= 1, "version", "1 이상의 정수")
    extra = doc.keys() - {"version", "table", "metric"}
    _need(not extra, "지표 목록", f"모르는 키 {sorted(extra)}")
    tables = doc.get("table", [])
    metrics = doc.get("metric", [])
    _need(isinstance(tables, list) and isinstance(metrics, list), "지표 목록", "table, metric은 [[...]] 배열")
    return Catalog(version, tuple(_table(t, k) for k, t in enumerate(tables)),
                   tuple(_metric(m, k) for k, m in enumerate(metrics)))  # fmt: skip


# --- 검사 (MC-7 1번) ----------------------------------------------------------------


def _unit_problem(m: Metric) -> str | None:
    suffix = m.name.rsplit("_", 1)[-1]
    want = UNIT_SUFFIX.get(suffix)
    if want is not None and m.unit != want:
        return f"{m.id}: 이름의 단위 접미 _{suffix}는 unit {want!r}인데 {m.unit!r}"
    if want is None and m.unit not in UNITLESS:
        return f"{m.id}: 단위 접미가 없는 이름은 unit이 {sorted(UNITLESS)} 중 하나인데 {m.unit!r}"
    if m.unit == "ratio" and m.range is not None and not (0.0 <= m.range[0] and m.range[1] <= 1.0):
        return f"{m.id}: 비율(unit ratio)의 range는 0–1 안이어야 함"
    return None


def _period_problems(m: Metric, catalog: Catalog) -> list[str]:
    out = []
    if m.names:
        out.append(f"{m.id}: names는 gaitfusion 지표만")
    if m.rule not in RULES:
        out.append(f"{m.id}: rule {m.rule!r}는 RP-3에 없음(가능한 것 {sorted(RULES)})")
    if m.by_side is None or m.min_n is None:
        out.append(f"{m.id}: period 지표에는 by_side와 min_n이 있어야 함")
    if m.from_ == BOUT_DURATION and m.by_side:
        out.append(f"{m.id}: from {BOUT_DURATION}에는 발이 없으므로 by_side = false여야 함")
    if m.rule == "given":
        if m.from_ is not None:
            out.append(f"{m.id}: rule given에는 from이 없어야 함")
    elif m.from_ != BOUT_DURATION:
        src = catalog.metric(m.from_) if m.from_ else None
        if src is None or src.level == PERIOD:
            out.append(f"{m.id}: from {m.from_!r}는 줄의 지표 id나 {BOUT_DURATION}여야 함")
        floor = 30 if m.rule in {"cv", "sd"} else 10
        low = m.where.get("bout_min_s", 0)
        if not _is_num(low) or low < floor:
            out.append(f"{m.id}: 줄의 값에서 계산하는 지표는 where.bout_min_s ≥ {floor}초(RP-3)")
    for key, value in m.where.items():
        if key not in WHERE_KEYS:
            out.append(f"{m.id}: where의 {key!r}는 RP-3에 없음(가능한 것 {sorted(WHERE_KEYS)})")
        elif key == "mode" and value not in MODES:
            out.append(f"{m.id}: where.mode {value!r}는 {sorted(MODES)} 중 하나여야 함")
        elif key != "mode" and not (_is_num(value) and value >= 0):
            out.append(f"{m.id}: where.{key}는 0 이상의 수")
    lo, hi = m.where.get("bout_min_s"), m.where.get("bout_max_s")
    if _is_num(lo) and _is_num(hi) and lo >= hi:
        out.append(f"{m.id}: where.bout_min_s < bout_max_s여야 함")
    return out


def check(catalog: Catalog) -> list[str]:
    """지표 목록 자체의 검사(MC-7 1번). 어긴 것마다 한 줄. 빈 목록이면 통과."""
    out: list[str] = []
    levels: dict[str, str] = {}
    fields: set[str] = set()
    for t in catalog.tables:
        if t.field in fields:
            out.append(f"table {t.field}: field가 겹침")
        fields.add(t.field)
        if t.level in levels or t.level == PERIOD:
            out.append(f"table {t.field}: level {t.level!r}가 겹치거나 period임")
        levels[t.level] = t.field
        for fmt in t.formats:
            m = FORMAT_RE.match(fmt)
            if m is None or m["level"] != t.level:
                out.append(f"table {t.field}: formats의 {fmt!r}는 'gaitfusion.{t.level}/<판>' 꼴이어야 함")

    seen: set[str] = set()
    for m in catalog.metrics:
        if m.id in seen:
            out.append(f"{m.id}: id가 겹침")
        seen.add(m.id)
        if not ID_RE.match(m.id) or m.id.split(".", 1)[0] != m.level:
            out.append(f"{m.id}: id는 '<level>.<이름>' 꼴이고 앞부분이 level({m.level})과 같아야 함")
        if m.level != PERIOD and m.level not in levels:
            out.append(f"{m.id}: level {m.level!r}의 [[table]]이 없음")
        if (problem := _unit_problem(m)) is not None:
            out.append(problem)
        out += [f"{m.id}: sources의 {s!r}는 '<첫 저자 성 소문자><연도>' 꼴이어야 함"
                for s in m.sources if not SOURCE_RE.match(s)]  # fmt: skip
        if len(set(m.sources)) != len(m.sources):
            out.append(f"{m.id}: sources에 같은 key가 두 번")
        if m.level == PERIOD:
            if m.producer != "server":
                out.append(f"{m.id}: period 지표의 producer는 server")
            out += _period_problems(m, catalog)
            continue
        if m.producer != "gaitfusion":
            out.append(f"{m.id}: 줄의 지표의 producer는 gaitfusion")
        if m.from_ is not None or m.rule is not None or m.where or m.by_side is not None or m.min_n is not None:
            out.append(f"{m.id}: from·rule·where·by_side·min_n은 period 지표만")
        table = catalog.table_by_level(m.level)
        for fmt in m.names:
            if table is None or fmt not in table.formats:
                out.append(f"{m.id}: names의 {fmt!r}가 그 표의 formats에 없음")
    return out


# --- 응답과 줄의 풀이 (MC-3, FT-3, FT-4) ---------------------------------------------


def formats_of(summary: object, catalog: Catalog) -> dict[str, str]:
    """응답의 표마다 형식(MC-3). summary.formats가 밝힌 것이 이기고, 밝히지 않은 목록의 표는
    gaitfusion.<level>/1(이 필드가 없던 지난 응답). 목록에 없는 표라도 밝힌 형식은 그대로 둠.
    형식이 글자가 아닌 항목은 없는 것으로 봄(받는 것은 넓게, MC-4)."""
    out = {t.field: f"gaitfusion.{t.level}/1" for t in catalog.tables}
    declared = summary.get("formats") if isinstance(summary, Mapping) else None
    if isinstance(declared, Mapping):
        out |= {k: v for k, v in declared.items() if isinstance(k, str) and isinstance(v, str)}
    return out


def _value(m: Metric, raw: object, scale: float) -> object | None:
    """형과 범위가 맞으면 환산한 값, 아니면 None."""
    if m.type == "text":
        return raw if isinstance(raw, str) else None
    if m.type == "integer":
        if type(raw) is not int:
            return None
        value: float | int = raw if scale == 1.0 else raw * scale
    else:
        if type(raw) not in (int, float) or not math.isfinite(raw):
            return None
        value = raw * scale if scale != 1.0 else raw
    if m.range is not None and not (m.range[0] <= value <= m.range[1]):
        return None
    return value


def _table_of(catalog: Catalog, level: str) -> Table:
    table = catalog.table_by_level(level)
    if table is None:
        raise KeyError(f"지표 목록에 level {level!r}의 [[table]]이 없음")
    return table


def normalize(catalog: Catalog, level: str, fmt: str, record: Mapping) -> dict[str, object]:
    """저장된 줄 하나 → {지표 id: 값}(FT-4). names로 이름을 옮기고 배수로 환산하고, 형이 틀리거나 유한하지 않거나
    범위 밖이면 그 값은 없음. 필드가 없거나 null이어도 값 없음. 목록의 formats에 없는 판이면 아무것도 풀지 않음."""
    if fmt not in _table_of(catalog, level).formats:
        return {}
    out: dict[str, object] = {}
    for m in catalog.metrics_of(level):
        name, scale = m.field_in(fmt)
        raw = record.get(name)
        if raw is not None and (value := _value(m, raw, scale)) is not None:
            out[m.id] = value
    return out


def inspect(catalog: Catalog, level: str, fmt: str, record: Mapping) -> tuple[int, list[str]]:
    """줄 하나를 지표 목록으로 본 결과(FT-3): (형이나 범위가 틀린 값의 수, 목록에 없는 필드 이름 정렬).
    null은 틀린 값이 아님(MC-2). 시작·끝·발 필드는 목록에 없어도 모르는 필드가 아님. 목록의 formats에 없는 판이면
    그 판의 어떤 필드도 목록이 모르므로 시작·끝·발 말고는 모두 모르는 필드."""
    table = _table_of(catalog, level)
    keys = set(table.keys.values())
    if fmt not in table.formats:
        return 0, sorted(k for k in record if k not in keys)
    known = keys.copy()
    bad = 0
    for m in catalog.metrics_of(level):
        name, scale = m.field_in(fmt)
        known.add(name)
        raw = record.get(name)
        if raw is not None and _value(m, raw, scale) is None:
            bad += 1
    return bad, sorted(k for k in record if k not in known)


# --- 기간의 지표 값 (RP-3) ------------------------------------------------------------

STRIDE = "stride"  # 주 발과 보고할 발을 정하는 줄의 종류(RP-3 "보폭 줄이 더 많은 발")
SIDES = ("left", "right")  # 보고하는 순서
GIVEN = {"period.wear_time_s": "wear_time_s"}  # rule given: 지표 id → period_values의 인자


@dataclass(frozen=True)
class Record:
    """저장된 줄 하나를 normalize로 푼 것. values의 키는 지표 id(예 stride.duration_s)."""

    side: str | None
    values: Mapping[str, object]


@dataclass(frozen=True)
class BoutData:
    """기간에 든 걷기 구간 하나(RP-2가 고름). records는 줄의 종류(level) → 그 구간의 줄."""

    duration_s: float
    mode: str  # fused · imu_only
    records: Mapping[str, Sequence[Record]] = field(default_factory=dict)

    def feet(self) -> frozenset[str]:
        """이 구간의 보폭 줄에 나온 발(left·right)."""
        return frozenset(r.side for r in self.records.get(STRIDE, ()) if r.side in SIDES)

    def main_foot(self) -> str | None:
        """주 발: 보폭 줄이 더 많은 발, 같으면 왼발. 값이 null인 줄도 셈. 왼발·오른발 줄이 없으면 None(발 없는 줄)."""
        sides = [r.side for r in self.records.get(STRIDE, ())]
        left, right = sides.count("left"), sides.count("right")
        if left == right == 0:
            return None
        return "left" if left >= right else "right"


@dataclass(frozen=True)
class Value:
    """지표 값 하나(RP-3의 형식). value가 None이면 reason(no_data · needs_both_feet)."""

    metric: str
    side: str | None
    value: float | int | None
    unit: str
    n: int
    sufficient: bool
    trust: str
    where: Mapping[str, object]
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        out = {"metric": self.metric, "side": self.side, "value": self.value, "unit": self.unit, "n": self.n,
               "sufficient": self.sufficient, "trust": self.trust, "where": dict(self.where)}  # fmt: skip
        if self.value is None:
            out["reason"] = self.reason
        return out


def _result(m: Metric, side: str | None, value: float | int | None, n: int, reason: str = "no_data") -> Value:
    if value is None:
        return Value(m.id, side, None, m.unit, 0, False, m.trust, dict(m.where), reason)
    return Value(m.id, side, value, m.unit, n, n >= (m.min_n or 1), m.trust, dict(m.where))


def _in_where(where: Mapping[str, object], b: BoutData) -> bool:
    """where의 조건: bout_min_s 이상, bout_max_s 미만, mode가 같음. 없으면 전부."""
    lo, hi, mode = where.get("bout_min_s"), where.get("bout_max_s"), where.get("mode")
    return (lo is None or b.duration_s >= lo) and (hi is None or b.duration_s < hi) and (mode is None or b.mode == mode)


def _quantile(values: Sequence[float], q: float) -> float:
    """가까운 두 순위 사이의 선형 보간(statistics.quantiles의 inclusive, numpy 기본과 같음)."""
    xs = sorted(values)
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def _spread(rule: str, xs: Sequence[float]) -> float | None:
    """걷기 구간 하나의 표본 표준편차(sd) 또는 변동 계수(cv). 값이 3개보다 적거나 평균이 0이면(cv) None."""
    if len(xs) < 3:
        return None
    sd = statistics.stdev(xs)
    if rule == "sd":
        return sd
    mean = statistics.fmean(xs)
    return None if mean == 0 else sd / mean


def _of_bout(m: Metric, level: str | None, b: BoutData, side: str | None) -> list[float]:
    """걷기 구간 하나에서 이 지표의 from 값들. 줄의 값은 그 발(by_side) 또는 주 발의 줄만, null은 뺌."""
    if m.from_ == BOUT_DURATION:
        return [b.duration_s]
    foot = side if m.by_side else b.main_foot()
    return [v for r in b.records.get(level, ()) if r.side == foot and (v := r.values.get(m.from_)) is not None]


def _compute(m: Metric, level: str | None, bouts: Sequence[BoutData], side: str | None) -> Value:
    """rule 하나의 계산(RP-3의 표). bouts는 기간의 모든 걷기 구간."""
    if m.needs == "both_feet":
        both = [b for b in bouts if b.feet() == frozenset(SIDES)]
        if bouts and not both:
            return _result(m, side, None, 0, "needs_both_feet")
        bouts = both
    per_bout = [_of_bout(m, level, b, side) for b in bouts if _in_where(m.where, b)]

    if m.rule == "ratio":  # where에 맞는 구간의 합 ÷ 모든 구간의 합. n은 모든 구간의 수
        total = math.fsum(math.fsum(_of_bout(m, level, b, side)) for b in bouts)
        if total <= 0:
            return _result(m, side, None, 0)
        return _result(m, side, math.fsum(math.fsum(xs) for xs in per_bout) / total, len(bouts))
    if m.rule in {"cv", "sd"}:  # 구간마다 내고 그 중앙값. n은 쓴 구간의 수
        spreads = [x for xs in per_bout if (x := _spread(m.rule, xs)) is not None]
        return _result(m, side, _quantile(spreads, 0.5) if spreads else None, len(spreads))

    values = [x for xs in per_bout for x in xs]
    if m.rule == "cadence":  # 보폭 시간마다 120 ÷ 보폭 시간
        values = [120.0 / d for d in values if d > 0]
    if not values:
        return _result(m, side, None, 0)
    if m.rule == "count":
        return _result(m, side, len(values), len(values))
    if m.rule == "sum":
        value = math.fsum(values)
    elif m.rule == "max":
        value = float(max(values))
    else:
        value = _quantile(values, {"median": 0.5, "cadence": 0.5, "p90": 0.9, "p95": 0.95}[m.rule])
    return _result(m, side, value, len(values))


def period_values(catalog: Catalog, bouts: Sequence[BoutData], *, wear_time_s: float | None) -> list[Value]:
    """기간의 걷기 구간과 그 줄 → period 지표마다 값(RP-3). by_side 지표는 발마다 하나.

    순수 함수: DB·파일을 모르고 입력을 바꾸지 않으며, 걷기 구간과 줄의 순서에 결과가 매이지 않습니다.
    일별 값(SM)과 보고서(RP)가 같은 숫자를 얻도록 둘 다 이것만 부릅니다. 돌려주는 순서는 지표 목록의 순서,
    발은 왼발·오른발. 보고할 발은 where와 상관없이 기간의 보폭 줄에 나온 발이고, 하나도 없으면 발 없는 no_data 값 하나.
    """
    bouts = tuple(bouts)
    feet = [side for side in SIDES if any(side in b.feet() for b in bouts)]
    given = {"wear_time_s": wear_time_s}
    out: list[Value] = []
    for m in catalog.metrics_of(PERIOD):
        if m.rule == "given":
            if m.id not in GIVEN:
                raise ValueError(f"{m.id}: rule given인데 넘겨받는 값이 정해지지 않음(GIVEN에 없음)")
            value = given[GIVEN[m.id]]
            out.append(_result(m, None, None if value is None else float(value), 1))
            continue
        if m.rule not in RULES:
            raise ValueError(f"{m.id}: rule {m.rule!r}는 RP-3에 없음")
        level = None
        if m.from_ != BOUT_DURATION:
            src = catalog.metric(m.from_) if m.from_ else None
            if src is None:
                raise ValueError(f"{m.id}: from {m.from_!r}가 지표 목록에 없음")
            level = src.level
        if not m.by_side:
            out.append(_compute(m, level, bouts, None))
        elif level is None:
            raise ValueError(f"{m.id}: by_side는 줄의 값에서 계산하는 지표만(from이 {BOUT_DURATION})")
        elif not feet:
            out.append(_result(m, None, None, 0))
        else:
            out += [_compute(m, level, bouts, side) for side in feet]
    return out


# --- DB 행 → 걷기 구간 (SM-3, RP-2) ------------------------------------------------------


def bouts_from_rows(
    catalog: Catalog,
    bouts: Iterable[Mapping],
    records: Iterable[Mapping],
    formats: Mapping[int, object],
) -> list[BoutData]:
    """고른 걷기 구간의 행과 그 줄의 행 → period_values의 입력(FT-4의 풀이를 거친 BoutData).

    일별 값과 보고서가 같은 숫자를 얻도록 둘 다 이것을 씁니다(단계끼리는 import하지 않으므로 여기 둠).
    - bouts: analysis.bout 행 {id, run_id, start_at, end_at, mode}. 돌려주는 순서도 이 순서.
    - records: feature.record 행 {bout_id, kind, side, v}. 지표 목록에 [[table]]이 없는 종류는 버림.
    - formats: run_id → 그 run의 feature.unpack.formats. 없거나 None이면 아직 풀리지 않은 run이므로 그 구간의 줄은 없음
      (걷기 구간 자체는 횟수·시간에 셈). 형식을 밝히지 않은 표는 formats_of대로 /1.
    """
    by_bout: dict[int, dict[str, list[Record]]] = {}
    for r in records:
        by_bout.setdefault(r["bout_id"], {}).setdefault(r["kind"], []).append(r)
    out = []
    for b in bouts:
        declared = formats.get(b["run_id"])
        recs: dict[str, tuple[Record, ...]] = {}
        if declared is not None:
            fmts = formats_of({"formats": declared}, catalog)
            for kind, rows in by_bout.get(b["id"], {}).items():
                table = catalog.table_by_level(kind)
                if table is None:
                    continue
                fmt = fmts[table.field]
                recs[kind] = tuple(Record(r["side"], normalize(catalog, kind, fmt, r["v"])) for r in rows)
        out.append(BoutData((b["end_at"] - b["start_at"]).total_seconds(), b["mode"], recs))
    return out
