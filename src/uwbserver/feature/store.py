"""feature의 SQL. 함수 하나 = 쿼리 하나. 쓰기는 feature.*만: 줄과 풀기 기록을 넣고, 다시 풀 때 그 run의 것을 지움(FT-5)."""

import psycopg
from psycopg.types.json import Jsonb


def run_info(conn: psycopg.Connection, run_id: int) -> dict | None:
    return conn.execute(
        "SELECT id, status, origin_at, result_path, result_sha256 FROM analysis.run WHERE id = %s", (run_id,)
    ).fetchone()


def bout_ids(conn: psycopg.Connection, run_id: int) -> dict[int, int]:
    """응답 bouts[]의 순번 → analysis.bout.id."""
    rows = conn.execute("SELECT idx, id FROM analysis.bout WHERE run_id = %s", (run_id,)).fetchall()
    return {r["idx"]: r["id"] for r in rows}


def delete_records(conn: psycopg.Connection, run_id: int) -> int:
    cur = conn.execute(
        "DELETE FROM feature.record WHERE bout_id IN (SELECT id FROM analysis.bout WHERE run_id = %s)", (run_id,)
    )
    return cur.rowcount


def delete_unpack(conn: psycopg.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM feature.unpack WHERE run_id = %s", (run_id,))


_RECORD_COLS = ("bout_id", "kind", "idx", "side", "start_at", "end_at", "v")


def copy_records(conn: psycopg.Connection, rows: list[dict]) -> None:
    """FT-2: COPY 한 번."""
    if not rows:
        return
    with conn.cursor().copy(f"COPY feature.record ({', '.join(_RECORD_COLS)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([Jsonb(r["v"]) if c == "v" else r[c] for c in _RECORD_COLS])


_UNPACK_COLS = (
    "run_id", "catalog_version", "formats", "n_records", "n_dropped_records", "n_bad_values", "unknown_fields",
    "unknown_tables", "core_version", "unpacked_at",
)  # fmt: skip


def insert_unpack(conn: psycopg.Connection, row: dict) -> None:
    values = [Jsonb(row[c]) if c == "formats" else row[c] for c in _UNPACK_COLS]
    conn.execute(
        f"INSERT INTO feature.unpack ({', '.join(_UNPACK_COLS)}) VALUES ({', '.join(['%s'] * len(_UNPACK_COLS))})",
        values,
    )


# ---------------------------------------------------------------- 앱 조회 (14.8절)


def bout(conn: psycopg.Connection, bout_id: int) -> dict | None:
    """걷기 구간과 귀속된 착용자(SG-9), 그 run을 푼 기록의 형식. 풀지 않았으면 formats = NULL."""
    return conn.execute(
        """
        SELECT b.id, b.run_id, bs.subject_id, u.formats
        FROM analysis.bout b
        JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        LEFT JOIN feature.unpack u ON u.run_id = b.run_id
        WHERE b.id = %s
        """,
        (bout_id,),
    ).fetchone()


def records_of_bout(conn: psycopg.Connection, bout_id: int, kind: str) -> list[dict]:
    return conn.execute(
        """
        SELECT idx, side, start_at, end_at, v FROM feature.record
        WHERE bout_id = %s AND kind = %s
        ORDER BY idx
        """,
        (bout_id, kind),
    ).fetchall()
