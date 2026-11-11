"""시험 공용 준비물.

DB 시험은 compose.dev의 PostgreSQL(localhost:5432)에 시험용 DB `uwb_test`를 새로 만들고
migrations/를 적용해 씁니다. 이름은 환경 변수 TEST_DB_NAME으로 바꿀 수 있습니다(같은 DB 서버에서 시험을 둘 이상 동시에 돌릴 때). 시험마다 표를 비우고, 파일 뿌리는 tmp 폴더, 시계는 FakeClock입니다.
DB가 없으면 db 시험은 건너뜁니다:  docker compose -f compose.yaml -f compose.dev.yaml up -d db
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from uwbserver.pipeline import clock, migrate, settings
from uwbserver.pipeline.settings import Role, Settings

TEST_DB = os.environ.get("TEST_DB_NAME", "uwb_test")
T0 = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)


def _dotenv() -> dict[str, str]:
    p = Path(__file__).resolve().parents[1] / ".env"
    out: dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def base_settings() -> Settings:
    env = {**_dotenv(), **os.environ}
    env.setdefault("DB_PASSWORD", "dev-owner")
    env.setdefault("DB_INTAKE_PASSWORD", "dev-intake")
    env.setdefault("DB_CORE_PASSWORD", "dev-core")
    env.setdefault("DB_API_PASSWORD", "dev-api")
    env["DB_HOST"] = os.environ.get("TEST_DB_HOST", "localhost")
    env["DB_PORT"] = os.environ.get("TEST_DB_PORT", "5432")
    env["DB_NAME"] = TEST_DB
    return Settings.from_env(env)


def _admin_conn(s: Settings) -> psycopg.Connection:
    return psycopg.connect(s.dsn("owner", dbname="postgres"), autocommit=True)


@pytest.fixture(scope="session")
def database() -> Iterator[Settings]:
    s = base_settings()
    try:
        admin = _admin_conn(s)
    except psycopg.OperationalError as e:
        pytest.skip(f"시험 DB에 연결 못 함({e}). compose.dev의 db를 띄우세요.")
    with admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    settings.use(s)
    try:
        migrate.run(dbname=TEST_DB)
    finally:
        settings.use(None)
    yield s


_TABLES_SQL = """
SELECT format('%I.%I', schemaname, tablename) AS t FROM pg_tables
WHERE schemaname IN ('pipeline','registry','intake','segment','analysis','archive','feature','summary')
  AND NOT (schemaname = 'pipeline' AND tablename = 'migration')
"""


@dataclass
class Env:
    """시험 하나의 환경: 설정, 시계, 파일 뿌리, 역할별 연결."""

    settings: Settings
    clock: clock.FakeClock
    data_root: Path
    _conns: list[psycopg.Connection] = field(default_factory=list)

    def conn(self, role: Role = "owner", *, autocommit: bool = True) -> psycopg.Connection:
        from psycopg.rows import dict_row

        c = psycopg.connect(self.settings.dsn(role), row_factory=dict_row, autocommit=autocommit)
        self._conns.append(c)
        return c

    def q(self, query: str, params=None) -> list[dict]:
        """owner로 읽기 한 번(검사용)."""
        cur = self._owner().execute(query, params)
        return cur.fetchall() if cur.description else []

    def _owner(self) -> psycopg.Connection:
        if not hasattr(self, "_owner_conn"):
            self._owner_conn = self.conn("owner", autocommit=True)
        return self._owner_conn

    def one(self, query: str, params=None):
        rows = self.q(query, params)
        assert len(rows) == 1, rows
        return rows[0]

    def scalar(self, query: str, params=None):
        row = self.one(query, params)
        return next(iter(row.values()))

    def close(self) -> None:
        for c in self._conns:
            c.close()


@pytest.fixture
def fake_clock() -> Iterator[clock.FakeClock]:
    c = clock.FakeClock(T0)
    clock.use(c)
    yield c
    clock.use(None)


@pytest.fixture
def env(database: Settings, tmp_path: Path, fake_clock: clock.FakeClock) -> Iterator[Env]:
    s = replace(database, data_root=tmp_path / "data")
    s.data_root.mkdir()
    settings.use(s)
    with psycopg.connect(s.dsn("owner"), autocommit=True) as c:
        tables = [r[0] for r in c.execute(_TABLES_SQL).fetchall()]
        c.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        c.execute("INSERT INTO pipeline.cursor VALUES ('chunk', 0)")
    e = Env(s, fake_clock, s.data_root)
    yield e
    e.close()
    settings.use(None)


@pytest.fixture
def device(env):
    from tests.support import register

    return register(env.conn("api"))


@pytest.fixture
def intake_client(env):
    from fastapi.testclient import TestClient

    from uwbserver.process.intake import create_app

    with TestClient(create_app()) as client:
        yield client
