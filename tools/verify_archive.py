"""archive 파일과 archive.range 대조, 빠진 행 다시 만들기 (SPEC TL-4, 5.2, OP-3, OP-4).

    uv run python tools/verify_archive.py [--reindex] [--prefix /data/archive/<장치>/<YYYY-MM>]

검사: archive 파일마다 sha256과 크기를 archive.range 행과 비교합니다. 다음을 문제로 셉니다.
  sha256_mismatch, bytes_mismatch  파일 내용이 행과 다름(파일이 바뀌었거나 깨짐)
  name_mismatch                     경로의 장치·부팅·seq 범위가 행과 다름
  no_row                            파일은 있는데 행이 없음(--reindex로 다시 만듦)
  missing_file                      행은 있는데 파일이 없음
  bad_name                          경로가 5.2절 archive 이름 꼴이 아님

--reindex: 행이 없는 파일에서 행을 다시 만듭니다(5.2절 마지막 항목).
  device_id·head_boot_id·seq 범위는 경로에서, n_chunks·bytes·sha256은 파일에서 얻습니다.
  segment_id는 (device_id, head_boot_id, seq 범위)를 덮는 구간입니다. 그런 구간이 없으면(DB를 복구한 직후)
  건너뛰고 no_segment로 셉니다. core가 raw 재색인분을 처리한 뒤에 다시 돌리면 됩니다.
  start_at·end_at은 archive.run이 쓰는 규칙 그대로 구간의 utc_offset_us로 셉니다:
  start_at = 첫 묶음 head_us_start + utc_offset_us, end_at = 마지막 묶음 head_us_start + utc_offset_us + 1초.
  그 범위를 만든 run은 알 수 없으므로 from_run_id = NULL입니다.

DB 역할은 core_rw(--reindex의 INSERT 포함)이고 파일 뿌리는 DATA_ROOT(기본 /data)입니다.
  컨테이너 안에서:  docker compose exec core python tools/verify_archive.py

archive 작업이 도는 중에도 돌릴 수 있습니다. 행이 없어 보이는 파일은 archive 작업과 같은 장치 잠금을 잡고
다시 확인하므로, 막 쓰인 파일(파일을 쓴 뒤 행을 넣기 전)을 문제로 세지 않습니다.

종료 코드: 0 = 문제 없음(--reindex로 되살린 행 포함), 1 = 문제가 남음, 2 = 인자 오류나 DB에 닿지 못함.
"""

import argparse
import hashlib
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import psycopg

from uwbserver.archive import store as archive_store
from uwbserver.pipeline import clock, db, files, log, wire

ARCHIVE_PREFIX = f"{files.LOGICAL_ROOT}/archive/"


# ---------------------------------------------------------------- SQL (이 도구만 씀)


def list_rows(conn: psycopg.Connection, prefix: str) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT id, path, bytes, sha256, device_id, head_boot_id, first_seq, last_seq
        FROM archive.range
        WHERE left(path, length(%(p)s)) = %(p)s
        """,
        {"p": prefix},
    ).fetchall()
    return {r["path"]: r for r in rows}


def row_by_path(conn: psycopg.Connection, path: str) -> dict | None:
    return conn.execute(
        """
        SELECT id, path, bytes, sha256, device_id, head_boot_id, first_seq, last_seq
        FROM archive.range WHERE path = %s
        """,
        (path,),
    ).fetchone()


def covering_segment(
    conn: psycopg.Connection, device_id: str, head_boot_id: int, first_seq: int, last_seq: int
) -> dict | None:
    """seq 범위 전체를 덮는 구간. 구간은 같은 부팅 안에서 겹치지 않으므로 많아야 하나."""
    return conn.execute(
        """
        SELECT id, utc_offset_us
        FROM segment.segment
        WHERE device_id = %s AND head_boot_id = %s AND first_seq <= %s AND last_seq >= %s
        ORDER BY first_seq DESC
        LIMIT 1
        """,
        (device_id, head_boot_id, first_seq, last_seq),
    ).fetchone()


# ---------------------------------------------------------------- 검사


@dataclass
class Report:
    files: int = 0
    rows: int = 0
    ok: int = 0
    rebuilt: int = 0
    problems: Counter = field(default_factory=Counter)

    def problem(self, kind: str, path: str, **detail) -> None:
        self.problems[kind] += 1
        log.error("archive_problem", kind=kind, path=path, **detail)


def digest(path: str) -> tuple[bytes, int]:
    with open(files.resolve(path), "rb") as f:
        h = hashlib.file_digest(f, "sha256")
        return h.digest(), f.tell()


def check_against_row(report: Report, path: str, row: dict) -> None:
    sha, size = digest(path)
    bad = False
    if size != row["bytes"]:
        report.problem("bytes_mismatch", path, row_bytes=row["bytes"], file_bytes=size)
        bad = True
    if sha != bytes(row["sha256"]):
        report.problem("sha256_mismatch", path, row_sha256=bytes(row["sha256"]), file_sha256=sha)
        bad = True
    try:
        name = files.parse_archive_path(path)
        key = (name.device_id, name.head_boot_id, name.first_seq, name.last_seq)
        if key != (row["device_id"], row["head_boot_id"], row["first_seq"], row["last_seq"]):
            report.problem("name_mismatch", path, row_id=row["id"])
            bad = True
    except ValueError:
        report.problem("bad_name", path, row_id=row["id"])
        bad = True
    if not bad:
        report.ok += 1


def rebuild_row(conn: psycopg.Connection, report: Report, path: str, name: files.ArchiveName) -> dict | None:
    """파일에서 archive.range 행을 만듭니다. 만들 수 없으면 문제를 적고 None."""
    data = files.resolve(path).read_bytes()
    records, valid_end = files.scan_raw(path)
    if valid_end != len(data):
        report.problem("torn_file", path, valid_bytes=valid_end, file_bytes=len(data))
        return None
    if not records:
        report.problem("empty_file", path)
        return None
    headers = []
    for rec in records:
        try:
            headers.append(wire.decode_chunk(rec.data).header)
        except ValueError as e:
            report.problem("unreadable_chunk", path, offset=rec.offset, error=str(e))
            return None
    seqs = [h.chunk_seq for h in headers]
    if (
        any(h.head_boot_id != name.head_boot_id for h in headers)
        or seqs != sorted(set(seqs))
        or seqs[0] != name.first_seq
        or seqs[-1] != name.last_seq
    ):
        report.problem("content_mismatch", path, first_seq=seqs[0], last_seq=seqs[-1], n_chunks=len(seqs))
        return None
    seg = covering_segment(conn, name.device_id, name.head_boot_id, name.first_seq, name.last_seq)
    if seg is None:
        report.problem(
            "no_segment",
            path,
            hint="덮는 구간이 아직 없음. core가 raw 재색인분을 처리한 뒤 --reindex를 다시 돌리세요",
        )
        return None
    start_us = headers[0].head_us_start + seg["utc_offset_us"]
    end_us = headers[-1].head_us_start + seg["utc_offset_us"] + 1_000_000
    if abs(start_us - clock.to_us(name.start_at)) >= 1_000_000:
        # 구간의 시각 기준이 보관 뒤에 바뀐 경우(SG-7). 행은 지금 구간 기준으로 만듦.
        log.warning("archive_reindex_start_differs", path=path, name_start=name.start_at, start_us=start_us)
    return {
        "device_id": name.device_id,
        "head_boot_id": name.head_boot_id,
        "segment_id": seg["id"],
        "first_seq": name.first_seq,
        "last_seq": name.last_seq,
        "n_chunks": len(records),
        "start_at": clock.from_us(start_us),
        "end_at": clock.from_us(end_us),
        "path": path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).digest(),
        "from_run_id": None,
        "created_at": clock.now(),
    }


def handle_rowless(conn: psycopg.Connection, report: Report, path: str, reindex: bool) -> None:
    try:
        name = files.parse_archive_path(path)
    except ValueError:
        report.problem("bad_name", path)
        return
    try:
        with conn.transaction():
            # archive 작업과 같은 장치 잠금: 막 쓰인 파일이면 그 작업이 행을 넣고 끝낼 때까지 기다림
            archive_store.lock_device(conn, name.device_id)
            row = row_by_path(conn, path)
            if row is not None:
                check_against_row(report, path, row)
                return
            if not reindex:
                report.problem("no_row", path)
                return
            new = rebuild_row(conn, report, path, name)
            if new is None:
                return
            archive_store.insert_range(conn, new)
    except psycopg.IntegrityError as e:
        # EXCLUDE(같은 부팅 안 seq 겹침) 따위: 다른 행이 이 범위를 이미 덮음
        report.problem("overlap", path, error=str(e).strip().splitlines()[0])
        return
    report.rebuilt += 1
    log.info("archive_row_rebuilt", path=path, segment=new["segment_id"], n_chunks=new["n_chunks"])


def verify(conn: psycopg.Connection, *, prefix: str, reindex: bool) -> Report:
    report = Report()
    # 행을 먼저, 파일을 나중에 읽음. 파일을 쓴 뒤에 행을 넣으므로 이 순서면 "행은 있는데 파일이 없음"을 잘못 보지 않음.
    rows = list_rows(conn, prefix)
    report.rows = len(rows)
    paths = [p for p in files.list_files("archive") if p.startswith(prefix)]
    report.files = len(paths)
    for path in paths:
        row = rows.pop(path, None)
        if row is not None:
            check_against_row(report, path, row)
        else:
            handle_rowless(conn, report, path, reindex)
    for path, row in sorted(rows.items()):
        if files.exists(path):  # 목록을 읽은 뒤 나타난 파일은 없음. 그래도 있으면 대조
            check_against_row(report, path, row)
        else:
            report.problem("missing_file", path, row_id=row["id"])
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verify_archive.py",
        description="archive 파일의 sha256을 archive.range와 대조합니다(TL-4).",
        epilog="종료 코드: 0 문제 없음, 1 문제가 남음, 2 인자 오류·접속 실패.",
    )
    p.add_argument("--reindex", action="store_true", help="행이 없는 파일에서 행을 다시 만듦(from_run_id = NULL)")
    p.add_argument(
        "--prefix",
        default=ARCHIVE_PREFIX,
        metavar="PATH",
        help="이 논리 경로로 시작하는 파일과 행만(예: /data/archive/head-e072a1aca858/2026-10). "
        "복구 연습에서 archive 일부만 받았을 때 씀",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.prefix.startswith(ARCHIVE_PREFIX.rstrip("/")):
        parser.error(f"--prefix는 {ARCHIVE_PREFIX}로 시작해야 함")
    log.setup("verify_archive")
    try:
        with db.connect("core") as conn:
            report = verify(conn, prefix=args.prefix, reindex=args.reindex)
    except (psycopg.Error, OSError) as e:
        log.error("verify_archive_failed", error=f"{type(e).__name__}: {e}")
        return 2
    log.info(
        "verify_archive_done",
        prefix=args.prefix,
        reindex=args.reindex,
        files=report.files,
        rows=report.rows,
        ok=report.ok,
        rebuilt=report.rebuilt,
        problems=dict(report.problems),
    )
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
