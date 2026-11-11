"""feature 단계의 순수 규칙: 결과 원문 → feature.record 행과 feature.unpack의 수 (SPEC 14.5 FT-2, FT-3, MC-4).

줄(보폭 하나, 걸음 하나, 회전 하나, 10초 창 하나, 삽화 하나)은 낸 그대로 v에 둡니다. 이름·단위·범위의 풀이는
읽을 때 pipeline/metrics.py가 합니다(FT-4). 여기서는 지표 목록으로 "보기"만 하고(inspect) 값을 고르거나 바꾸지
않습니다. 바꾸는 것은 하나뿐: JSON의 NaN·Infinity는 jsonb에 넣을 수 없으므로 null(MC-2의 "계산하지 못함")로 둡니다.

- 줄을 버리는 경우(FT-3): 시작·끝이 유한한 수가 아님(필드가 없음, null, 글자, bool, NaN, 무한), 끝이 시작보다
  앞섬, 시각으로 나타낼 수 없을 만큼 큼, 발 값이 left·right·null(또는 없음)이 아님, 줄이 객체가 아님.
  idx는 버린 줄이 있어도 배열 안 순번 그대로입니다(결과 원문의 그 항목을 가리킴).
- unknown_fields: 저장한 줄마다 metrics.inspect가 모른다고 한 필드를 '<배열 이름>.<필드>'로. 겹치지 않게, 정렬.
- unknown_tables: 걷기 구간 안의 배열 중 (1) 지표 목록의 [[table]]에 없고 (2) 1단계가 푸는 배열(trajectories)이
  아니고 (3) 항목이 모두 객체인 것(빈 배열 포함)의 이름. 풀지 않습니다(결과 원문에는 남음). 항목이 수나 글자인
  배열은 줄의 표가 아니므로 세지 않습니다. 걷기 구간 밖(최상위 models[] 등)은 보지 않습니다(14.1의 3단계).
- 지표 목록의 표인데 배열이 없거나 배열이 아니면(null 등) 그 종류의 줄이 없는 것입니다(MC-4 "표가 없어짐").
"""

import gzip
import hashlib
import json
import math
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from uwbserver.pipeline import metrics

PHASE1_ARRAYS = frozenset({"trajectories"})  # 7.2절이 푸는 배열. 줄의 표가 아님
SIDES = ("left", "right")


class ResultInvalid(ValueError):
    """결과 원문 파일을 쓸 수 없음(gzip이 깨짐, sha256이 run과 다름, JSON이 아님)."""


class UnpackError(ValueError):
    """결과 원문과 DB가 어긋남(예: 원문의 걷기 구간이 analysis.bout에 없음)."""


@dataclass
class Unpacked:
    """run 하나를 푼 것. rows = feature.record 행(id 제외)."""

    rows: list[dict] = field(default_factory=list)
    formats: dict[str, str] = field(default_factory=dict)
    n_dropped_records: int = 0
    n_bad_values: int = 0
    unknown_fields: list[str] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)

    @property
    def n_records(self) -> int:
        return len(self.rows)

    @property
    def has_warnings(self) -> bool:
        """FT-3의 경고 로그 조건. 버린 줄도 넣음(잃은 자료이므로)."""
        return bool(self.n_dropped_records or self.n_bad_values or self.unknown_fields or self.unknown_tables)


def read_result(gz: bytes, want_sha256: bytes) -> dict:
    """결과 원문 파일 바이트(gzip) → JSON. sha256은 압축 전 바이트로 run의 값과 대조(AN-10, FT-2)."""
    try:
        raw = gzip.decompress(gz)
    except (OSError, EOFError, zlib.error) as e:
        raise ResultInvalid(f"gzip: {e}") from None
    if hashlib.sha256(raw).digest() != bytes(want_sha256):
        raise ResultInvalid("sha256: 결과 원문이 run의 result_sha256과 다름")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise ResultInvalid(f"json: {e}") from None
    if not isinstance(doc, dict):
        raise ResultInvalid(f"json: 최상위가 {type(doc).__name__}")
    return doc


def _finite(x: object) -> bool:
    if type(x) not in (int, float):  # bool은 수가 아님
        return False
    try:
        return math.isfinite(x)
    except OverflowError:
        return False


def _at(origin_at: datetime, s: float) -> datetime:
    """analysis.bout와 같은 규칙: origin_at + 초(µs로 반올림)."""
    return origin_at + timedelta(microseconds=round(s * 1_000_000))


def _jsonb_safe(x: object) -> object:
    """NaN·무한 → None. 나머지는 그대로."""
    if type(x) is float and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _jsonb_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonb_safe(v) for v in x]
    return x


def _row(rec: object, table: metrics.Table, *, bout_id: int, idx: int, origin_at: datetime) -> dict | None:
    """줄 하나 → 행. 버릴 줄이면 None."""
    if not isinstance(rec, dict):
        return None
    start, end = rec.get(table.keys["start"]), rec.get(table.keys["end"])
    side = rec.get(table.keys["side"])
    if not (_finite(start) and _finite(end)) or end < start:
        return None
    if side is not None and side not in SIDES:
        return None
    try:
        start_at, end_at = _at(origin_at, start), _at(origin_at, end)
    except OverflowError:
        return None
    return {"bout_id": bout_id, "kind": table.level, "idx": idx, "side": side, "start_at": start_at,
            "end_at": end_at, "v": _jsonb_safe(rec)}  # fmt: skip


def _is_record_table(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def unpack(result: Mapping, catalog: metrics.Catalog, *, origin_at: datetime, bout_ids: Mapping[int, int]) -> Unpacked:
    """결과 원문(JSON) → 행과 수. bout_ids = 응답 bouts[]의 순번 → analysis.bout.id.

    행의 차례: 걷기 구간 순, 그 안에서 지표 목록의 [[table]] 순, 그 안에서 배열 순."""
    bouts = result.get("bouts")
    if not isinstance(bouts, list):
        raise UnpackError("결과 원문에 bouts 배열이 없음")
    if len(bouts) != len(bout_ids) or set(bout_ids) != set(range(len(bouts))):
        raise UnpackError(f"결과 원문의 걷기 구간 {len(bouts)}개와 analysis.bout {len(bout_ids)}개가 다름")
    formats = metrics.formats_of(result.get("summary"), catalog)
    out = Unpacked(formats=formats)
    unknown_fields: set[str] = set()
    unknown_tables: set[str] = set()
    known = {t.field for t in catalog.tables} | PHASE1_ARRAYS
    for k, b in enumerate(bouts):
        if not isinstance(b, dict):  # 1단계 검사(7.2)를 지난 원문이면 없음
            raise UnpackError(f"bouts[{k}]가 객체가 아님")
        unknown_tables |= {name for name, value in b.items() if name not in known and _is_record_table(value)}
        for table in catalog.tables:
            records = b.get(table.field)
            if not isinstance(records, list):
                continue
            fmt = formats[table.field]
            for idx, rec in enumerate(records):
                row = _row(rec, table, bout_id=bout_ids[k], idx=idx, origin_at=origin_at)
                if row is None:
                    out.n_dropped_records += 1
                    continue
                n_bad, names = metrics.inspect(catalog, table.level, fmt, rec)
                out.n_bad_values += n_bad
                unknown_fields.update(f"{table.field}.{n}" for n in names)
                out.rows.append(row)
    out.unknown_fields = sorted(unknown_fields)
    out.unknown_tables = sorted(unknown_tables)
    return out
