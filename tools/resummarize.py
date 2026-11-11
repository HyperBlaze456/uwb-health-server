"""지난 날짜의 일별 값을 다시 계산하게 함 (SPEC TL-11, SM-4).

    uv run python tools/resummarize.py --since 2026-10-01
    uv run python tools/resummarize.py --since 2026-10-01 --device head-aabbccddeeff

그 날짜부터 오늘(한국 날짜)까지 착용 기록이 있는 장치마다 `summary {장치, since, 오늘}` 작업 하나를 넣습니다. 계산은 core의
summary 작업 스레드가 합니다(그 장치를 찬 착용자 전원, 날짜마다). 지표 목록에 period 지표를 더하거나 names를 고친 뒤에
씁니다. 표는 그대로이고, 다시 계산은 그 착용자·날짜의 행을 지우고 새로 넣으므로 몇 번 돌려도 같습니다.

  --since 날짜   한국 날짜(YYYY-MM-DD). 오늘보다 뒤면 인자 오류
  --device 장치  그 장치만(예 head-aabbccddeeff)

같은 대상의 summary 작업이 이미 대기 중이면 넣지 않고 셉니다(SM-1).
DB 역할은 core_rw입니다.  컨테이너 안에서:  docker compose exec core python tools/resummarize.py --since 2026-10-01

종료 코드: 0 = 넣음(넣을 것이 없어도 0), 2 = 인자 오류나 DB에 닿지 못함.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date

import psycopg

from uwbserver.pipeline import clock, db, jobs, log


def worn_devices(conn: psycopg.Connection, since: date, device_id: str | None) -> list[str]:
    """since(한국 날짜)부터 지금까지와 겹치는 착용 기록이 있는 장치(이 도구만 쓰는 SQL)."""
    rows = conn.execute(
        """
        SELECT DISTINCT device_id FROM registry.wear
        WHERE (ended_at IS NULL OR ended_at > %(start)s) AND (%(device)s::text IS NULL OR device_id = %(device)s)
        ORDER BY device_id
        """,
        {"start": clock.day_start(since), "device": device_id},
    ).fetchall()
    return [r["device_id"] for r in rows]


def enqueue(conn: psycopg.Connection, since: date, device_id: str | None) -> tuple[int, int]:
    """(넣은 작업 수, 이미 대기 중이라 넣지 않은 수)."""
    queued = waiting = 0
    today = clock.today()
    for dev in worn_devices(conn, since, device_id):
        with conn.transaction():
            if jobs.enqueue_summary(conn, dev, since, today, now=clock.now()):
                queued += 1
            else:
                waiting += 1
    return queued, waiting


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜(YYYY-MM-DD)가 아님: {text!r}") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="지난 날짜의 summary 작업을 넣음 (TL-11, SM-4)")
    p.add_argument("--since", type=_date, required=True, help="이 한국 날짜부터 오늘까지 (예 2026-10-01)")
    p.add_argument("--device", help="이 장치만 (예 head-aabbccddeeff)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.setup("resummarize")
    if args.since > clock.today():
        print(f"--since {args.since}가 오늘({clock.today()})보다 뒤임", file=sys.stderr)
        return 2
    try:
        with db.connect("core") as conn:
            queued, waiting = enqueue(conn, args.since, args.device)
    except (psycopg.Error, OSError) as e:
        log.error("resummarize_failed", error=f"{type(e).__name__}: {e}")
        return 2
    log.info("resummarize_done", since=args.since.isoformat(), device=args.device, queued=queued,
             already_waiting=waiting)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
