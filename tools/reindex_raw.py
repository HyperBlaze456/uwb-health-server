"""raw 파일 → intake.* 행 다시 만들기 (SPEC TL-3, OP-4).

    uv run python tools/reindex_raw.py [--since 2026-10-26T03:00Z] [--device head-e072a1aca858]

raw 파일(/data/raw/<장치>/<부팅>/<hour>.bin)을 처음부터 읽어 intake.chunk와 블록 행 중 없는 키만 넣습니다.
이미 있는 키는 건너뛰므로 몇 번을 돌려도 결과가 같습니다. intake 시작 때 재색인(IN-9)과 같은 코드
(intake.run.reindex)를 씁니다. 다만 이 도구는 intake가 받는 중에도 돌 수 있으므로 끝이 잘린 레코드를
잘라 내지 않습니다(그 일은 intake 시작 때만 함).

DB 역할은 intake_rw이고 접속 정보는 프로세스와 같은 DB_* 환경 변수입니다. 파일 뿌리는 DATA_ROOT(기본 /data)입니다.
  컨테이너 안에서:  docker compose run --rm intake python tools/reindex_raw.py
  호스트에서:      DB_HOST=localhost DATA_ROOT=/srv/uwb uv run python tools/reindex_raw.py

종료 코드: 0 = 모든 레코드를 읽음, 1 = 읽지 못한 레코드가 있음(나머지는 넣음), 2 = 인자 오류나 DB·파일에 닿지 못함.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

import psycopg

from uwbserver.intake import run as intake_run
from uwbserver.pipeline import db, log


def parse_utc(text: str) -> datetime:
    """ISO 8601 시각. 'Z'나 시간대가 없으면 UTC로 봅니다."""
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"ISO 8601 시각이 아님: {text!r}") from e
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reindex_raw.py",
        description="raw 파일에서 intake.* 행을 다시 만듭니다(TL-3). 이미 있는 키는 건너뜁니다.",
        epilog="종료 코드: 0 정상, 1 읽지 못한 레코드 있음, 2 인자 오류·접속 실패.",
    )
    p.add_argument(
        "--since",
        type=parse_utc,
        metavar="ISO",
        help="이 시각이 든 hour 파일부터(UTC). 없으면 raw 파일 전부",
    )
    p.add_argument("--device", metavar="ID", help="이 장치만(예: head-e072a1aca858)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.setup("reindex_raw")
    # 파일 이름의 hour는 그 hour의 시작이므로, --since가 든 hour의 파일도 읽도록 hour 단위로 내림
    since_hour = args.since.replace(minute=0, second=0, microsecond=0) if args.since else None
    log.info("reindex_raw_start", since_hour=since_hour, device=args.device)
    try:
        with db.connect("intake") as conn:
            stats = intake_run.reindex(conn, since_hour=since_hour, device_id=args.device, repair_tail=False)
    except (psycopg.Error, OSError) as e:
        log.error("reindex_raw_failed", error=f"{type(e).__name__}: {e}")
        return 2
    for where in stats.unreadable:
        log.error("raw_record_unreadable", where=where)
    log.info(
        "reindex_raw_done",
        files=stats.files,
        records=stats.records,
        inserted=stats.inserted,
        unreadable=len(stats.unreadable),
    )
    return 1 if stats.unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
