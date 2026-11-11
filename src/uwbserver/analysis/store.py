"""analysis의 SQL. 함수 하나 = 쿼리 하나. 쓰기는 analysis.*만(결과는 추가만, 궤적 행 삭제만 예외)."""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True, slots=True)
class SegmentView:
    """analysis가 보는 구간."""

    id: int
    device_id: str
    head_boot_id: int
    state: str
    sides: str
    first_seq: int
    last_seq: int
    n_chunks: int
    revision: int
    utc_offset_us: int
    installation_id: int | None


def lock_segment(conn: psycopg.Connection, segment_id: int) -> SegmentView | None:
    """구간 행을 잠그고 읽습니다(AN-2, AN-4). 구간을 닫거나 묶음을 더하는 segment 트랜잭션과 차례를 정해,
    "열려 있어 run 없이 끝냄"과 "닫히며 작업 넣기"가 엇갈려 작업 없이 닫힌 구간이 남지 않게 합니다.
    잠금은 입력을 고르는 짧은 트랜잭션 동안만입니다(C2 호출 중에는 풀려 있음)."""
    row = conn.execute(
        """
        SELECT id, device_id, head_boot_id, state, sides, first_seq, last_seq, n_chunks, revision, utc_offset_us,
               installation_id
        FROM segment.segment WHERE id = %s
        FOR UPDATE
        """,
        (segment_id,),
    ).fetchone()
    return SegmentView(**row) if row else None


def segment_revision(conn: psycopg.Connection, segment_id: int) -> int:
    return conn.execute("SELECT revision FROM segment.segment WHERE id = %s", (segment_id,)).fetchone()["revision"]


def installation(conn: psycopg.Connection, installation_id: int) -> dict | None:
    return conn.execute(
        "SELECT surveyed, anchors FROM segment.installation WHERE id = %s", (installation_id,)
    ).fetchone()


def subject_of_segment(conn: psycopg.Connection, segment_id: int) -> dict | None:
    """SG-9 뷰로 정한 착용자의 키·다리 길이·마비측. 주인이 없거나 모호하면 None."""
    return conn.execute(
        """
        SELECT s.height_m, s.leg_length_m, s.paretic_side
        FROM analysis.segment_subject ss JOIN registry.subject s ON s.id = ss.subject_id
        WHERE ss.segment_id = %s
        """,
        (segment_id,),
    ).fetchone()


def raw_chunks(conn: psycopg.Connection, seg: SegmentView) -> list[dict]:
    """구간의 묶음 행(seq 순). 위치와 Head 시각만.

    - 프레임 없는 묶음(SG-2)은 seq가 구간 범위 안에 있어도 구간에 들지 않으므로 뺍니다.
    - segment가 아직 처리하지 않은 묶음(id > 읽기 위치)도 뺍니다. 구간의 n_chunks에 아직 없기 때문입니다.
    """
    return conn.execute(
        """
        SELECT c.chunk_seq, c.head_us_start, c.raw_path, c.raw_offset, c.raw_len
        FROM intake.chunk c, pipeline.cursor k
        WHERE c.device_id = %s AND c.head_boot_id = %s AND c.chunk_seq BETWEEN %s AND %s
          AND (c.n_range > 0 OR c.n_imu_frames > 0)
          AND k.name = 'chunk' AND c.id <= k.last_id
        ORDER BY c.chunk_seq
        """,
        (seg.device_id, seg.head_boot_id, seg.first_seq, seg.last_seq),
    ).fetchall()


def archive_ranges(conn: psycopg.Connection, segment_id: int) -> list[dict]:
    return conn.execute(
        "SELECT path, first_seq, last_seq, n_chunks FROM archive.range WHERE segment_id = %s ORDER BY first_seq",
        (segment_id,),
    ).fetchall()


def next_run_id(conn: psycopg.Connection) -> int:
    """AN-6: 호출 전에 run 번호를 받음. 실패한 시도의 번호는 버림."""
    return conn.execute("SELECT nextval('analysis.run_id_seq') AS id").fetchone()["id"]


def next_bout_ids(conn: psycopg.Connection, n: int) -> list[int]:
    """COPY로 넣을 걷기 구간의 id를 미리 받습니다(궤적 행이 bout id를 가리키므로)."""
    if n == 0:
        return []
    rows = conn.execute("SELECT nextval('analysis.bout_id_seq') AS id FROM generate_series(1, %s)", (n,)).fetchall()
    return [r["id"] for r in rows]


_RUN_COLS = (
    "id", "segment_id", "segment_revision", "input", "n_chunks_sent", "origin_head_us", "origin_at",
    "gaitfusion_version", "core_version", "context", "summary", "status", "skip_reason", "result_status",
    "result_path", "result_bytes", "result_sha256", "started_at", "finished_at",
)  # fmt: skip


def insert_run(conn: psycopg.Connection, run: dict) -> None:
    values = [Jsonb(run[c]) if c in ("context", "summary") and run[c] is not None else run[c] for c in _RUN_COLS]
    conn.execute(
        f"INSERT INTO analysis.run ({', '.join(_RUN_COLS)}) VALUES ({', '.join(['%s'] * len(_RUN_COLS))})", values
    )


_BOUT_COLS = ("id", "run_id", "idx", "start_at", "end_at", "mode", "sides", "quality")
_TRAJ_COLS = ("bout_id", "side", "path", "rate_hz", "t0", "n", "bytes")


def copy_bouts(conn: psycopg.Connection, rows: list[dict]) -> None:
    """9절: 걷기 구간 행은 COPY."""
    if not rows:
        return
    with conn.cursor().copy(f"COPY analysis.bout ({', '.join(_BOUT_COLS)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([Jsonb(r[c]) if c == "quality" else r[c] for c in _BOUT_COLS])


def copy_trajectories(conn: psycopg.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor().copy(f"COPY analysis.trajectory ({', '.join(_TRAJ_COLS)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r[c] for c in _TRAJ_COLS])


def old_trajectories(conn: psycopg.Connection, segment_id: int, before_run_id: int) -> list[dict]:
    """같은 구간의 옛 run 궤적(캐시, 5.1절)."""
    return conn.execute(
        """
        SELECT t.bout_id, t.side, t.path, b.run_id
        FROM analysis.trajectory t
        JOIN analysis.bout b ON b.id = t.bout_id
        JOIN analysis.run r ON r.id = b.run_id
        WHERE r.segment_id = %s AND r.id < %s
        """,
        (segment_id, before_run_id),
    ).fetchall()


def delete_trajectory(conn: psycopg.Connection, bout_id: int, side: str) -> None:
    conn.execute("DELETE FROM analysis.trajectory WHERE bout_id = %s AND side = %s", (bout_id, side))


# ---------------------------------------------------------------- 앱 조회 (C3, 8절)


def run(conn: psycopg.Connection, run_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT r.id, r.segment_id, r.segment_revision, r.input, r.n_chunks_sent, r.origin_head_us, r.origin_at,
               r.gaitfusion_version, r.core_version, r.context, r.summary, r.status, r.skip_reason,
               r.result_status, r.result_path, r.result_bytes, encode(r.result_sha256, 'hex') AS result_sha256,
               r.started_at, r.finished_at,
               (cr.run_id IS NOT NULL) AS is_current
        FROM analysis.run r
        LEFT JOIN analysis.current_run cr ON cr.run_id = r.id
        WHERE r.id = %s
        """,
        (run_id,),
    ).fetchone()


def unpack_of_run(conn: psycopg.Connection, run_id: int) -> dict | None:
    """feature 단계가 이 run을 푼 기록(FT-3, 14.8절). 아직 풀지 않았으면 None."""
    return conn.execute(
        """
        SELECT n_records, n_dropped_records, n_bad_values, unknown_fields, unknown_tables, catalog_version, formats,
               unpacked_at
        FROM feature.unpack WHERE run_id = %s
        """,
        (run_id,),
    ).fetchone()


def bouts_of_run(conn: psycopg.Connection, run_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT b.id, b.idx, b.start_at, b.end_at, b.mode, b.sides, b.quality,
               coalesce(array_agg(t.side ORDER BY t.side) FILTER (WHERE t.side IS NOT NULL), '{}') AS trajectory_sides
        FROM analysis.bout b LEFT JOIN analysis.trajectory t ON t.bout_id = b.id
        WHERE b.run_id = %s
        GROUP BY b.id ORDER BY b.idx
        """,
        (run_id,),
    ).fetchall()


def runs_of_segment(conn: psycopg.Connection, segment_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT id, status, skip_reason, result_status, input, gaitfusion_version, started_at, finished_at
        FROM analysis.run WHERE segment_id = %s ORDER BY id DESC
        """,
        (segment_id,),
    ).fetchall()


def subject_bouts(
    conn: psycopg.Connection, subject_id: int, start: datetime, end: datetime, limit: int, before_id: int | None
) -> list[dict]:
    """현재 run의 걷기 구간 중 그 착용자에게 귀속된 것(SG-9). 기간으로 먼저 좁힌 뒤 뷰를 씀(9절)."""
    return conn.execute(
        """
        SELECT b.id, b.run_id, r.segment_id, b.start_at, b.end_at, b.sides, b.mode, r.result_status
        FROM analysis.bout b
        JOIN analysis.current_run cr ON cr.run_id = b.run_id
        JOIN analysis.run r ON r.id = b.run_id
        JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        WHERE b.start_at >= %s AND b.start_at < %s AND bs.subject_id = %s
          AND (%s::bigint IS NULL OR b.id < %s)
        ORDER BY b.start_at DESC, b.id DESC
        LIMIT %s
        """,
        (start, end, subject_id, before_id, before_id, limit),
    ).fetchall()


def bout(conn: psycopg.Connection, bout_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT b.id, b.run_id, b.idx, b.start_at, b.end_at, b.sides, b.mode, bs.subject_id
        FROM analysis.bout b JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        WHERE b.id = %s
        """,
        (bout_id,),
    ).fetchone()


def trajectories_of_bout(conn: psycopg.Connection, bout_id: int) -> list[dict]:
    return conn.execute(
        "SELECT side, path, rate_hz, t0, n, bytes FROM analysis.trajectory WHERE bout_id = %s ORDER BY side",
        (bout_id,),
    ).fetchall()
