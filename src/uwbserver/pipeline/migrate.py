"""마이그레이션 (PL-1). `python -m uwbserver.pipeline.migrate`

pipeline.migration(name, applied_at)을 먼저 만들고, migrations/의 아직 적용하지 않은 파일을
번호 순으로 하나씩 트랜잭션으로 적용해 기록합니다. 그 뒤 역할 비밀번호를 .env 값으로 맞춥니다.
다시 돌려도 아무것도 바뀌지 않습니다.
"""

import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from uwbserver.pipeline import clock, db, log, settings

_NAME = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")
ROLES = {"intake": "intake_rw", "core": "core_rw", "api": "api_rw"}


def migrations_dir() -> Path:
    env = os.environ.get("MIGRATIONS_DIR")
    if env:
        return Path(env)
    # 저장소: src/uwbserver/pipeline/migrate.py → 저장소 뿌리/migrations. 이미지에서는 /app/migrations.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("migrations/ 폴더를 찾지 못함")


def pending(conn: psycopg.Connection, folder: Path) -> list[Path]:
    applied = {r["name"] for r in conn.execute("SELECT name FROM pipeline.migration").fetchall()}
    files = sorted(p for p in folder.iterdir() if _NAME.match(p.name))
    return [p for p in files if p.name not in applied]


def run(*, dbname: str | None = None, folder: Path | None = None, retry_s: float = 0.0) -> list[str]:
    """적용한 파일 이름들을 돌려줍니다."""
    folder = folder or migrations_dir()
    s = settings.get()
    applied: list[str] = []
    with db.connect("owner", dbname=dbname, retry_s=retry_s) as conn:
        conn.autocommit = True
        conn.execute("SELECT pg_advisory_lock(%s, 0)", (db.MIGRATE_LOCK,))
        try:
            conn.execute("CREATE SCHEMA IF NOT EXISTS pipeline")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pipeline.migration (name text PRIMARY KEY, applied_at timestamptz NOT NULL)"
            )
            for path in pending(conn, folder):
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute(
                        "INSERT INTO pipeline.migration (name, applied_at) VALUES (%s, %s)",
                        (path.name, clock.now()),
                    )
                applied.append(path.name)
                log.info("migration_applied", name=path.name)
            for role, user in ROLES.items():
                password = s.db_role_password(role)
                exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
                if password and exists:
                    conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(user), sql.Literal(password)))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s, 0)", (db.MIGRATE_LOCK,))
    return applied


def main() -> int:
    log.setup("migrate")
    applied = run(retry_s=60.0)
    log.info("migrate_done", applied=applied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
