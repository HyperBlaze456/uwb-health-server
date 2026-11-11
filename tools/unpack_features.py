"""지난 run의 결과 원문을 다시 풀게 함 (SPEC TL-9, FT-6).

    uv run python tools/unpack_features.py --all
    uv run python tools/unpack_features.py --since 2026-11-01T00:00:00Z

결과 원문이 있는 run(status = done)마다 `feature {run_id}` 작업을 넣습니다. 푸는 일은 core의 feature 작업 스레드가 합니다.
2단계를 처음 켤 때(지난 run 채우기)와 지표 목록에 새 [[table]]을 더한 뒤(FT-5) 씁니다. 다시 풀기는 그 run의 행을
지우고 새로 넣으므로 몇 번 돌려도 같습니다.

  --all          모든 run
  --since 시각   그 시각 이후에 끝난 run(finished_at ≥ 시각). 시간대를 붙여 씀(예 Z, +09:00)

같은 run의 feature 작업이 이미 대기·실행 중이면 넣지 않고 셉니다(PL-2).
DB 역할은 core_rw입니다.  컨테이너 안에서:  docker compose exec core python tools/unpack_features.py --all

종료 코드: 0 = 넣음(넣을 것이 없어도 0), 2 = 인자 오류나 DB에 닿지 못함.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime

import psycopg

from uwbserver.pipeline import clock, db, jobs, log


def done_runs(conn: psycopg.Connection, since: datetime | None) -> list[int]:
    """결과 원문이 있는 run(이 도구만 쓰는 SQL)."""
    rows = conn.execute(
        """
        SELECT id FROM analysis.run
        WHERE status = 'done' AND result_path IS NOT NULL AND (%(since)s::timestamptz IS NULL OR finished_at >= %(since)s)
        ORDER BY id
        """,
        {"since": since},
    ).fetchall()
    return [r["id"] for r in rows]


def enqueue(conn: psycopg.Connection, since: datetime | None) -> tuple[int, int]:
    """(넣은 작업 수, 이미 대기·실행 중이라 넣지 않은 수)."""
    queued = waiting = 0
    for run_id in done_runs(conn, since):
        with conn.transaction():
            if jobs.enqueue_feature(conn, run_id, now=clock.now()):
                queued += 1
            else:
                waiting += 1
    return queued, waiting


def _aware(text: str) -> datetime:
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"ISO 8601 시각이 아님: {text!r}") from None
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"시간대를 붙이세요(예 {text}Z): {text!r}")
    return t


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="지난 run마다 feature 작업을 넣음 (TL-9, FT-6)")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="결과 원문이 있는 모든 run")
    which.add_argument("--since", type=_aware, help="이 시각 이후에 끝난 run (예 2026-11-01T00:00:00Z)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.setup("unpack_features")
    try:
        with db.connect("core") as conn:
            queued, waiting = enqueue(conn, None if args.all else args.since)
    except (psycopg.Error, OSError) as e:
        log.error("unpack_features_failed", error=f"{type(e).__name__}: {e}")
        return 2
    log.info("unpack_features_done", since=args.since, queued=queued, already_waiting=waiting)
    return 0


if __name__ == "__main__":
    sys.exit(main())
