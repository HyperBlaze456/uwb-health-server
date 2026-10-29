"""거절한 묶음 다시 넣기 (SPEC IN-10, TL-5).

    uv run python tools/reingest_rejected.py /data/rejected/head-e072a1aca858/2026-10-26.bin [--since ISO]

rejected 파일의 레코드를 받기와 같은 검사·쓰기 경로(intake.run.receive)로 다시 넣습니다. 서버의 해독기를
고친 뒤(예: 새 version 지원) 씁니다. 장치는 토큰이 아니라 파일 경로의 device_id로 정합니다.
통과한 묶음은 raw와 intake.chunk에 들어가고, rejected 레코드는 그대로 남습니다. 다시 거절된 것은 또 적지 않습니다.

파일은 논리 경로(/data/rejected/...)나, DATA_ROOT 아래의 실제 경로로 줄 수 있습니다.
DB 역할은 intake_rw입니다. 컨테이너 안에서:  docker compose run --rm intake python tools/reingest_rejected.py <파일>

출력: 표준 출력에는 한 줄 JSON 로그(묶음마다 intake_request, 끝에 reingest_rejected_done), 표준 오류에는
결과 코드별 수의 표.
종료 코드: 0 = 끝까지 돌림(다시 거절된 묶음이 있어도), 1 = 저장 실패(503)가 있어 일부를 넣지 못함,
2 = 인자 오류, 장치나 파일이 없음, DB에 닿지 못함.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from uwbserver.intake import run as intake_run
from uwbserver.pipeline import db, files, log, settings, wire
from uwbserver.registry import store as registry_store


def parse_utc(text: str) -> datetime:
    """ISO 8601 시각. 'Z'나 시간대가 없으면 UTC로 봅니다."""
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"ISO 8601 시각이 아님: {text!r}") from e
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def to_logical(arg: str) -> str:
    """'/data/rejected/...' 꼴이면 그대로, 아니면 DATA_ROOT 아래의 실제 경로로 보고 논리 경로로 바꿉니다."""
    if arg.startswith(files.LOGICAL_ROOT + "/"):
        return arg
    root = settings.get().data_root.resolve()
    try:
        rel = Path(arg).resolve().relative_to(root)
    except ValueError as e:
        raise ValueError(f"{arg}: DATA_ROOT({root}) 아래의 파일이 아님") from e
    return f"{files.LOGICAL_ROOT}/{rel.as_posix()}"


def code_name(code: int | str) -> str:
    if isinstance(code, str):
        return code  # 'framing', 'storage'
    try:
        return f"{int(code)} {wire.Code(code).name.lower()}"
    except ValueError:
        return str(code)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reingest_rejected.py",
        description="rejected 파일의 묶음을 받기와 같은 경로로 다시 넣습니다(IN-10).",
        epilog="종료 코드: 0 끝까지 돌림, 1 저장 실패가 있음, 2 인자 오류·장치나 파일 없음·접속 실패.",
    )
    p.add_argument("file", help="rejected 파일. /data/rejected/<장치>/<YYYY-MM-DD>.bin 또는 DATA_ROOT 아래 실제 경로")
    p.add_argument("--since", type=parse_utc, metavar="ISO", help="처음 받은 시각이 이 시각 이후인 레코드만(UTC)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.setup("reingest_rejected")
    try:
        path = to_logical(args.file)
        device_id, _day = files.parse_rejected_path(path)
    except ValueError as e:
        log.error("reingest_rejected_bad_path", error=str(e))
        return 2
    if not files.exists(path):
        log.error("reingest_rejected_no_file", path=path, real=str(files.resolve(path)))
        return 2
    try:
        with db.connect("intake") as conn:
            device = registry_store.device_by_id(conn, device_id)
            if device is None:
                log.error("reingest_rejected_no_device", device=device_id)
                return 2
            codes: Counter = intake_run.reingest_rejected(conn, device, path, since=args.since)
    except (psycopg.Error, OSError) as e:
        log.error("reingest_rejected_failed", error=f"{type(e).__name__}: {e}")
        return 2

    histogram = {code_name(k): n for k, n in sorted(codes.items(), key=lambda kv: str(kv[0]))}
    log.info("reingest_rejected_done", path=path, device=device_id, total=sum(codes.values()), codes=histogram)
    width = max((len(k) for k in histogram), default=4)
    print(f"{'code':<{width}}  count", file=sys.stderr)
    for name, n in histogram.items():
        print(f"{name:<{width}}  {n}", file=sys.stderr)
    return 1 if codes.get("storage") else 0


if __name__ == "__main__":
    sys.exit(main())
