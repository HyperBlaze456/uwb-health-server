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
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

# --- 정해진 이름 (MC-1, MC-2, RP-3) --------------------------------------------------

TYPES = frozenset({"number", "integer", "text"})
PRODUCERS = frozenset({"gaitfusion", "server"})
SHOW = frozenset({"basic", "detail"})
NEEDS = frozenset({"one_foot", "both_feet"})
TRUST = frozenset({"validated", "proxy", "low", "unverified"})
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
