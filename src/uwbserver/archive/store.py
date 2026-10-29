"""archive의 SQL. 쓰기는 archive.*(추가만), 예외로 30일 지난 intake 행 삭제(AR-6)."""

from datetime import datetime

import psycopg

from uwbserver.archive.rules import ChunkState, SegmentState, SeqRange

ARCHIVE_LOCK = 4  # 같은 장치의 보관 작업을 차례로(겹치는 범위를 두 작업이 함께 만들지 않게)


def lock_device(conn: psycopg.Connection, device_id: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (ARCHIVE_LOCK, device_id))


def run_info(conn: psycopg.Connection, run_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT r.id, r.segment_id, r.origin_head_us, r.origin_at, s.device_id, s.head_boot_id, s.first_seq,
               s.last_seq, s.utc_offset_us
        FROM analysis.run r JOIN segment.segment s ON s.id = r.segment_id
        WHERE r.id = %s
        """,
        (run_id,),
    ).fetchone()


def bouts(conn: psycopg.Connection, run_id: int) -> list[dict]:
    return conn.execute(
        "SELECT start_at, end_at FROM analysis.bout WHERE run_id = %s ORDER BY idx", (run_id,)
    ).fetchall()


def segment_chunks(conn: psycopg.Connection, run: dict) -> list[dict]:
    """구간의 intake.chunk 행(raw에 남아 있는 것만)."""
    return conn.execute(
        """
        SELECT chunk_seq, head_us_start, received_at, raw_path, raw_offset, raw_len
        FROM intake.chunk
        WHERE device_id = %s AND head_boot_id = %s AND chunk_seq BETWEEN %s AND %s
        ORDER BY chunk_seq
        """,
        (run["device_id"], run["head_boot_id"], run["first_seq"], run["last_seq"]),
    ).fetchall()


def covered(conn: psycopg.Connection, device_id: str, head_boot_id: int) -> list[SeqRange]:
    rows = conn.execute(
        "SELECT first_seq, last_seq FROM archive.range WHERE device_id = %s AND head_boot_id = %s",
        (device_id, head_boot_id),
    ).fetchall()
    return [SeqRange(r["first_seq"], r["last_seq"]) for r in rows]


def insert_range(conn: psycopg.Connection, row: dict) -> None:
    cols = (
        "device_id", "head_boot_id", "segment_id", "first_seq", "last_seq", "n_chunks", "start_at", "end_at",
        "path", "bytes", "sha256", "from_run_id", "created_at",
    )  # fmt: skip
    conn.execute(
        f"INSERT INTO archive.range ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        [row[c] for c in cols],
    )


def insert_cut(conn: psycopg.Connection, run_id: int, n_ranges: int, n_new_chunks: int, now: datetime) -> None:
    """AR-5: run마다 보관 끝 표시."""
    conn.execute(
        "INSERT INTO archive.cut (run_id, n_ranges, n_new_chunks, done_at) VALUES (%s, %s, %s, %s)",
        (run_id, n_ranges, n_new_chunks, now),
    )


def cut_exists(conn: psycopg.Connection, run_id: int) -> bool:
    return conn.execute("SELECT 1 FROM archive.cut WHERE run_id = %s", (run_id,)).fetchone() is not None


# ---------------------------------------------------------------- 30일 삭제 (AR-6)


def old_raw_paths(conn: psycopg.Connection, before: datetime) -> list[str]:
    """수신이 before 이전인 행이 가리키는 raw 파일(파일 목록과 합쳐 후보로 씀)."""
    rows = conn.execute(
        "SELECT DISTINCT raw_path FROM intake.chunk WHERE received_at < %s", (before,)
    ).fetchall()
    return [r["raw_path"] for r in rows]


def chunk_states(conn: psycopg.Connection, raw_path: str) -> list[ChunkState]:
    rows = conn.execute(
        """
        SELECT c.id, (c.n_range > 0 OR c.n_imu_frames > 0) AS framed, s.id AS segment_id
        FROM intake.chunk c
        LEFT JOIN segment.segment s
               ON s.device_id = c.device_id AND s.head_boot_id = c.head_boot_id
              AND c.chunk_seq BETWEEN s.first_seq AND s.last_seq
        WHERE c.raw_path = %s
        """,
        (raw_path,),
    ).fetchall()
    return [ChunkState(r["id"], r["framed"], r["segment_id"]) for r in rows]


def segment_states(conn: psycopg.Connection, segment_ids: list[int]) -> dict[int, SegmentState]:
    rows = conn.execute(
        """
        SELECT s.id, s.state,
               (SELECT count(*) FROM analysis.run r WHERE r.segment_id = s.id) AS n_runs,
               (SELECT count(*) FROM analysis.run r LEFT JOIN archive.cut k ON k.run_id = r.id
                 WHERE r.segment_id = s.id AND k.run_id IS NULL) AS n_runs_uncut,
               EXISTS (SELECT 1 FROM pipeline.job j WHERE j.kind = 'analysis' AND j.segment_id = s.id
                         AND j.state IN ('queued','running')) AS analysis_pending,
               EXISTS (SELECT 1 FROM pipeline.job j JOIN analysis.run r ON r.id = j.run_id
                        WHERE j.kind = 'archive' AND r.segment_id = s.id
                          AND j.state IN ('queued','running')) AS archive_pending,
               (SELECT j.state FROM pipeline.job j WHERE j.kind = 'analysis' AND j.segment_id = s.id
                 ORDER BY j.id DESC LIMIT 1) AS last_analysis_state
        FROM segment.segment s
        WHERE s.id = ANY(%s)
        """,
        (segment_ids,),
    ).fetchall()
    return {r["id"]: SegmentState(**r) for r in rows}


def count_rows_for_path(conn: psycopg.Connection, raw_path: str) -> dict:
    return conn.execute(
        """
        SELECT count(*) AS chunk,
               count(h.chunk_seq) AS health,
               count(y.chunk_seq) AS system
        FROM intake.chunk c
        LEFT JOIN intake.health h USING (device_id, head_boot_id, chunk_seq)
        LEFT JOIN intake.system y USING (device_id, head_boot_id, chunk_seq)
        WHERE c.raw_path = %s
        """,
        (raw_path,),
    ).fetchone()


def delete_blocks_for_path(conn: psycopg.Connection, raw_path: str) -> int:
    """AR-6 지우는 순서 1: health·system 행. survey는 지우지 않음(영구)."""
    n = conn.execute(
        """
        DELETE FROM intake.health h USING intake.chunk c
        WHERE c.raw_path = %s AND h.device_id = c.device_id AND h.head_boot_id = c.head_boot_id
          AND h.chunk_seq = c.chunk_seq
        """,
        (raw_path,),
    ).rowcount
    n += conn.execute(
        """
        DELETE FROM intake.system y USING intake.chunk c
        WHERE c.raw_path = %s AND y.device_id = c.device_id AND y.head_boot_id = c.head_boot_id
          AND y.chunk_seq = c.chunk_seq
        """,
        (raw_path,),
    ).rowcount
    return n


def delete_chunks_for_path(conn: psycopg.Connection, raw_path: str) -> int:
    """AR-6 지우는 순서 2: intake.chunk 행."""
    return conn.execute("DELETE FROM intake.chunk WHERE raw_path = %s", (raw_path,)).rowcount


def old_rejected_paths(conn: psycopg.Connection, before: datetime) -> list[str]:
    rows = conn.execute("SELECT DISTINCT path FROM intake.rejected WHERE received_at < %s", (before,)).fetchall()
    return [r["path"] for r in rows]


def count_rejected_for_path(conn: psycopg.Connection, path: str) -> int:
    return conn.execute("SELECT count(*) AS n FROM intake.rejected WHERE path = %s", (path,)).fetchone()["n"]


def delete_rejected_for_path(conn: psycopg.Connection, path: str) -> int:
    return conn.execute("DELETE FROM intake.rejected WHERE path = %s", (path,)).rowcount
