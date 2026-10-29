"""DB 연결과 잠금 (SPEC 2.2, PL-3, IN-4).

프로세스마다 DB 역할 하나로 접속합니다(4.2절). 트랜잭션은 `with conn.transaction():`으로 엽니다.
"""

import time
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.requests import Request

from uwbserver.pipeline import log, settings
from uwbserver.pipeline.settings import Role

# pg_advisory_xact_lock의 첫 키. 잠그는 순서는 늘 장치 → 전역(IN-4).
CHUNK_INSERT_LOCK = 1  # PL-3: intake.chunk INSERT 전역 잠금
DEVICE_LOCK = 2  # IN-4: 장치별 받기 줄 세우기
MIGRATE_LOCK = 3  # PL-1: migrate가 둘 동시에 돌지 않게


def connect(
    role: Role, *, dbname: str | None = None, retry_s: float = 0.0, autocommit: bool = True
) -> psycopg.Connection:
    """역할 하나로 연결합니다. retry_s > 0이면 DB가 뜰 때까지 그만큼 다시 시도합니다.

    기본은 autocommit입니다. 쓰기는 늘 `with conn.transaction():` 안에서 하고, 그 블록이 끝나면 커밋됩니다.
    """
    s = settings.get()
    deadline = time.monotonic() + retry_s
    while True:
        try:
            return psycopg.connect(s.dsn(role, dbname=dbname), row_factory=dict_row, autocommit=autocommit)
        except psycopg.OperationalError as e:
            if time.monotonic() >= deadline:
                raise
            log.warning("db_connect_retry", role=role, error=str(e).strip())
            time.sleep(1.0)


def pool(role: Role, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    s = settings.get()
    return ConnectionPool(
        s.dsn(role),
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
        name=f"uwb-{role}",
    )


def request_conn(request: Request) -> Iterator[psycopg.Connection]:
    """FastAPI 의존성: 프로세스가 app.state.pool에 둔 연결 묶음에서 하나를 빌려 줍니다."""
    with request.app.state.pool.connection() as conn:
        yield conn


def lock_chunk_insert(conn: psycopg.Connection) -> None:
    """PL-3: intake.chunk에 INSERT하는 모든 트랜잭션이 INSERT 직전에 잡습니다. 커밋할 때 풀림."""
    conn.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (CHUNK_INSERT_LOCK,))


def lock_device(conn: psycopg.Connection, device_id: str) -> None:
    """IN-4: 같은 장치의 받기를 차례로."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (DEVICE_LOCK, device_id))
