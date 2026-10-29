"""작업 큐와 읽기 위치 (PL-2, PL-3).

상태: queued → running → done | failed. 같은 대상의 queued·running 작업은 하나뿐(부분 고유 색인).
모든 시각은 호출하는 쪽이 clock에서 얻어 넘깁니다(PL-4).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import psycopg

Kind = Literal["analysis", "archive"]

MAX_RETRIES = 3  # 처음 시도까지 모두 네 번
DONE_KEEP_DAYS = 30


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    kind: Kind
    segment_id: int | None
    run_id: int | None
    state: str
    attempts: int
    last_error: str | None
    run_after: datetime
    created_at: datetime
    locked_by: str | None
    locked_at: datetime | None

    @classmethod
    def from_row(cls, row: dict) -> Job:
        return cls(**{k: row[k] for k in cls.__slots__})


_COLS = "id, kind, segment_id, run_id, state, attempts, last_error, run_after, created_at, locked_by, locked_at"


def enqueue_analysis(conn: psycopg.Connection, segment_id: int, *, now: datetime) -> bool:
    """analysis {segment_id}. 같은 구간의 대기·실행 작업이 있으면 넣지 않고 False."""
    cur = conn.execute(
        """
        INSERT INTO pipeline.job (kind, segment_id, run_after, created_at)
        VALUES ('analysis', %s, %s, %s)
        ON CONFLICT (segment_id) WHERE kind = 'analysis' AND state IN ('queued','running') DO NOTHING
        """,
        (segment_id, now, now),
    )
    return cur.rowcount == 1


def enqueue_archive(conn: psycopg.Connection, run_id: int, *, now: datetime) -> bool:
    cur = conn.execute(
        """
        INSERT INTO pipeline.job (kind, run_id, run_after, created_at)
        VALUES ('archive', %s, %s, %s)
        ON CONFLICT (run_id) WHERE kind = 'archive' AND state IN ('queued','running') DO NOTHING
        """,
        (run_id, now, now),
    )
    return cur.rowcount == 1


def claim(conn: psycopg.Connection, kinds: tuple[Kind, ...], *, worker: str, now: datetime) -> Job | None:
    """가져가기. 호출한 쪽이 곧바로 커밋해 running이 보이게 합니다."""
    row = conn.execute(
        f"""
        UPDATE pipeline.job SET state = 'running', locked_by = %s, locked_at = %s
        WHERE id = (SELECT id FROM pipeline.job
                    WHERE state = 'queued' AND run_after <= %s AND kind = ANY(%s)
                    ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING {_COLS}
        """,
        (worker, now, now, list(kinds)),
    ).fetchone()
    return Job.from_row(row) if row else None


def finish(conn: psycopg.Connection, job_id: int, *, now: datetime, note: str | None = None) -> None:
    """done. note는 run 없이 끝난 이유(예 'no_input', AN-4)."""
    conn.execute(
        "UPDATE pipeline.job SET state = 'done', finished_at = %s, last_error = %s WHERE id = %s",
        (now, note, job_id),
    )


def fail(conn: psycopg.Connection, job_id: int, error: str, *, now: datetime) -> None:
    """재시도하지 않을 실패."""
    conn.execute(
        "UPDATE pipeline.job SET state = 'failed', finished_at = %s, last_error = %s WHERE id = %s",
        (now, error[:2000], job_id),
    )


def retry_or_fail(conn: psycopg.Connection, job: Job, error: str, *, now: datetime) -> str:
    """재시도할 실패. k번째(1–3)면 2^k분 뒤로, 4번째면 failed. 새 상태를 돌려줍니다."""
    k = job.attempts + 1
    if k <= MAX_RETRIES:
        conn.execute(
            """
            UPDATE pipeline.job SET state = 'queued', attempts = %s, run_after = %s, last_error = %s,
                                    locked_by = NULL, locked_at = NULL
            WHERE id = %s
            """,
            (k, now + timedelta(minutes=2**k), error[:2000], job.id),
        )
        return "queued"
    conn.execute(
        "UPDATE pipeline.job SET state = 'failed', attempts = %s, finished_at = %s, last_error = %s WHERE id = %s",
        (k, now, error[:2000], job.id),
    )
    return "failed"


def defer(conn: psycopg.Connection, job_id: int, *, until: datetime, note: str) -> None:
    """미루기. attempts는 그대로(AN-3)."""
    conn.execute(
        """
        UPDATE pipeline.job SET state = 'queued', run_after = %s, last_error = %s, locked_by = NULL, locked_at = NULL
        WHERE id = %s
        """,
        (until, note, job_id),
    )


def revive_stale(conn: psycopg.Connection, *, now: datetime, stale_after_s: float) -> int:
    """오래된 running 작업을 실패 한 번으로 셉니다(PL-2 되살리기)."""
    rows = conn.execute(
        f"""
        SELECT {_COLS} FROM pipeline.job
        WHERE state = 'running' AND locked_at < %s
        FOR UPDATE SKIP LOCKED
        """,
        (now - timedelta(seconds=stale_after_s),),
    ).fetchall()
    for row in rows:
        retry_or_fail(conn, Job.from_row(row), "stale_running", now=now)
    return len(rows)


def cleanup(conn: psycopg.Connection, *, now: datetime) -> int:
    """30일 지난 done 작업 삭제. 원본이 아니므로 PURGE_ENABLED와 무관(PL-2).

    구간마다 가장 최근 analysis 작업은 남깁니다. AR-6이 "가장 최근 analysis 작업이 failed가 아님"을 보므로,
    그것을 지우면 그 앞의 옛 failed 작업이 가장 최근이 되어 원본을 영영 지우지 못하게 됩니다.
    """
    cur = conn.execute(
        """
        DELETE FROM pipeline.job j
        WHERE j.state = 'done' AND j.finished_at < %s
          AND NOT (j.kind = 'analysis'
                   AND j.id = (SELECT max(x.id) FROM pipeline.job x
                               WHERE x.kind = 'analysis' AND x.segment_id = j.segment_id))
        """,
        (now - timedelta(days=DONE_KEEP_DAYS),),
    )
    return cur.rowcount


def get(conn: psycopg.Connection, job_id: int) -> Job | None:
    row = conn.execute(f"SELECT {_COLS} FROM pipeline.job WHERE id = %s", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


# ---------------------------------------------------------------- 읽기 위치 (PL-3)


def get_cursor(conn: psycopg.Connection, name: str = "chunk", *, lock: bool = False) -> int:
    """lock=True는 트랜잭션 안에서 읽기 위치 행을 잠급니다(core가 둘 떠도 같은 묶음을 두 번 처리하지 않게)."""
    tail = " FOR UPDATE" if lock else ""
    return conn.execute(f"SELECT last_id FROM pipeline.cursor WHERE name = %s{tail}", (name,)).fetchone()["last_id"]


def set_cursor(conn: psycopg.Connection, last_id: int, name: str = "chunk") -> None:
    conn.execute("UPDATE pipeline.cursor SET last_id = %s WHERE name = %s", (last_id, name))
