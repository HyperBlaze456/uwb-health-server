"""PL-1 마이그레이션과 4.2절 역할 권한 (11절 공통)."""

import psycopg
import pytest

from uwbserver.pipeline import migrate, settings

pytestmark = pytest.mark.db


def _snapshot(conn) -> list[tuple]:
    return conn.execute(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema IN ('pipeline','registry','intake','segment','analysis','archive')
        ORDER BY 1, 2, 3
        """
    ).fetchall()


def test_rerun_changes_nothing(env):
    before = _snapshot(env.conn("owner"))
    applied_before = env.scalar("SELECT count(*) FROM pipeline.migration")
    assert migrate.run(dbname=env.settings.db_name) == []
    assert _snapshot(env.conn("owner")) == before
    assert env.scalar("SELECT count(*) FROM pipeline.migration") == applied_before == 7


def test_second_database_in_same_cluster(database):
    """역할은 클러스터 전체에 하나. 두 번째 빈 DB에 적용해도 오류가 없어야 함."""
    s = database
    with psycopg.connect(s.dsn("owner", dbname="postgres"), autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS uwb_test2 WITH (FORCE)")
        c.execute("CREATE DATABASE uwb_test2")
    settings.use(s)
    try:
        assert len(migrate.run(dbname="uwb_test2")) == 7
        assert migrate.run(dbname="uwb_test2") == []
    finally:
        settings.use(None)
        with psycopg.connect(s.dsn("owner", dbname="postgres"), autocommit=True) as c:
            c.execute("DROP DATABASE uwb_test2 WITH (FORCE)")


# 역할마다 쓰면 안 되는 곳(4.2절). (역할, SQL)
FORBIDDEN = [
    ("intake", "INSERT INTO segment.installation (device_id, geometry_hash, geometry_id, source, surveyed, anchors, first_seen_at, last_seen_at) VALUES ('x',1,'d','default',false,'[]',now(),now())"),
    ("intake", "UPDATE intake.chunk SET flags = 0"),
    ("intake", "DELETE FROM intake.chunk"),
    ("intake", "INSERT INTO pipeline.job (kind, segment_id) VALUES ('analysis', 1)"),
    ("intake", "UPDATE registry.device SET note = 'x'"),
    ("core", "UPDATE intake.chunk SET flags = 0"),
    ("core", "DELETE FROM intake.survey"),
    ("core", "UPDATE archive.range SET bytes = 0"),
    ("core", "DELETE FROM archive.range"),
    ("core", "DELETE FROM archive.cut"),
    ("core", "UPDATE analysis.run SET status = 'done'"),
    ("core", "DELETE FROM analysis.run"),
    ("core", "DELETE FROM analysis.bout"),
    ("core", "DELETE FROM segment.segment"),
    ("core", "INSERT INTO registry.subject (code) VALUES ('x')"),
    ("api", "INSERT INTO intake.rejected (device_id, received_at, code, path, file_offset, len) VALUES ('x', now(), 2, 'p', 0, 0)"),
    ("api", "UPDATE segment.segment SET state = 'open'"),
    ("api", "INSERT INTO analysis.bout (run_id, idx, start_at, end_at, mode, sides, quality) VALUES (1,0,now(),now(),'fused','left','{}')"),
    ("api", "DELETE FROM registry.subject"),
    ("api", "UPDATE pipeline.job SET state = 'done'"),
    ("api", "UPDATE pipeline.cursor SET last_id = 0"),
]


@pytest.mark.parametrize(("role", "statement"), FORBIDDEN)
def test_role_cannot_write_elsewhere(env, role, statement):
    conn = env.conn(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)
    conn.rollback()


def test_intake_commit_does_not_wait_for_disk(env):
    conn = env.conn("intake")
    assert conn.execute("SHOW synchronous_commit").fetchone()["synchronous_commit"] == "off"
