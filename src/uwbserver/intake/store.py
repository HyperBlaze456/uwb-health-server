"""intake의 SQL. 함수 하나 = 쿼리 하나(여러 행은 다중 행 INSERT 한 번). 쓰기는 intake.*만."""

from collections.abc import Sequence
from datetime import datetime

import psycopg

CHUNK_COLS = (
    "device_id", "head_boot_id", "chunk_seq", "received_at", "head_us_start", "utc_us_start", "geometry_hash",
    "flags", "n_range", "n_imu_frames", "dropped_range", "dropped_imu_frames", "status_mask", "tag_mask",
    "tag0_mac", "tag0_boot_id", "tag1_mac", "tag1_boot_id", "raw_path", "raw_offset", "raw_len",
)  # fmt: skip
BLOCK_COLS = {
    "health": ("device_id", "head_boot_id", "chunk_seq", "at", "flags", "buffer_used", "range_age_ms", "imu_age_ms", "block"),
    "system": ("device_id", "head_boot_id", "chunk_seq", "at", "tag_count", "geometry_hash", "firmware_id", "block"),
    "survey": ("device_id", "head_boot_id", "chunk_seq", "at", "solved", "survey_id", "revision", "block"),
}  # fmt: skip
REJECTED_COLS = (
    "device_id", "received_at", "code", "check_step", "detail", "head_boot_id", "chunk_seq", "path", "file_offset", "len",
)  # fmt: skip

_MAX_PARAMS = 60_000  # PostgreSQL 한 문장의 인자 한도(65535) 안쪽


def _insert(
    conn: psycopg.Connection, table: str, cols: Sequence[str], rows: Sequence[dict], *, skip_conflicts: bool
) -> int:
    """다중 행 INSERT. 행이 아주 많으면(재색인) 인자 한도에 맞게 나눕니다. 넣은 행 수를 돌려줍니다."""
    if not rows:
        return 0
    per = max(1, _MAX_PARAMS // len(cols))
    placeholder = "(" + ", ".join(["%s"] * len(cols)) + ")"
    tail = " ON CONFLICT DO NOTHING" if skip_conflicts else ""
    total = 0
    for i in range(0, len(rows), per):
        part = rows[i : i + per]
        query = f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ", ".join([placeholder] * len(part)) + tail
        params = [r[c] for r in part for c in cols]
        total += conn.execute(query, params).rowcount
    return total


def insert_chunks(conn: psycopg.Connection, rows: Sequence[dict], *, skip_conflicts: bool = False) -> int:
    return _insert(conn, "intake.chunk", CHUNK_COLS, rows, skip_conflicts=skip_conflicts)


def insert_health(conn: psycopg.Connection, rows: Sequence[dict], *, skip_conflicts: bool = False) -> int:
    return _insert(conn, "intake.health", BLOCK_COLS["health"], rows, skip_conflicts=skip_conflicts)


def insert_system(conn: psycopg.Connection, rows: Sequence[dict], *, skip_conflicts: bool = False) -> int:
    return _insert(conn, "intake.system", BLOCK_COLS["system"], rows, skip_conflicts=skip_conflicts)


def insert_survey(conn: psycopg.Connection, rows: Sequence[dict], *, skip_conflicts: bool = False) -> int:
    return _insert(conn, "intake.survey", BLOCK_COLS["survey"], rows, skip_conflicts=skip_conflicts)


def insert_rejected(conn: psycopg.Connection, rows: Sequence[dict]) -> int:
    return _insert(conn, "intake.rejected", REJECTED_COLS, rows, skip_conflicts=False)


def existing_keys(conn: psycopg.Connection, device_id: str, keys: Sequence[tuple[int, int]]) -> set[tuple[int, int]]:
    """IN-5: 요청 안 모든 키를 SELECT 한 번으로."""
    if not keys:
        return set()
    rows = conn.execute(
        """
        SELECT c.head_boot_id, c.chunk_seq
        FROM unnest(%s::bigint[], %s::bigint[]) AS k(boot, seq)
        JOIN intake.chunk c ON c.device_id = %s AND c.head_boot_id = k.boot AND c.chunk_seq = k.seq
        """,
        ([k[0] for k in keys], [k[1] for k in keys], device_id),
    ).fetchall()
    return {(r["head_boot_id"], r["chunk_seq"]) for r in rows}


def max_received_at(conn: psycopg.Connection) -> datetime | None:
    """IN-9의 기준."""
    return conn.execute("SELECT max(received_at) AS m FROM intake.chunk").fetchone()["m"]


# ---------------------------------------------------------------- 앱 조회 (C3, 8절)


def health_rows(conn: psycopg.Connection, device_id: str, start: datetime, end: datetime, limit: int) -> list[dict]:
    return conn.execute(
        """
        SELECT head_boot_id, chunk_seq, at, block FROM intake.health
        WHERE device_id = %s AND at >= %s AND at < %s
        ORDER BY at DESC LIMIT %s
        """,
        (device_id, start, end, limit),
    ).fetchall()


def survey_rows(conn: psycopg.Connection, device_id: str, limit: int, before: datetime | None) -> list[dict]:
    return conn.execute(
        """
        SELECT head_boot_id, chunk_seq, at, block FROM intake.survey
        WHERE device_id = %s AND (%s::timestamptz IS NULL OR at < %s)
        ORDER BY at DESC LIMIT %s
        """,
        (device_id, before, before, limit),
    ).fetchall()


def rejected_rows(
    conn: psycopg.Connection, device_id: str, start: datetime, end: datetime, limit: int, before_id: int | None
) -> list[dict]:
    return conn.execute(
        """
        SELECT id, received_at, code, check_step, detail, head_boot_id, chunk_seq, len FROM intake.rejected
        WHERE device_id = %s AND received_at >= %s AND received_at < %s AND (%s::bigint IS NULL OR id < %s)
        ORDER BY id DESC LIMIT %s
        """,
        (device_id, start, end, before_id, before_id, limit),
    ).fetchall()
