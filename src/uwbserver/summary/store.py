"""summary의 SQL. 함수 하나 = 쿼리 하나. 쓰기는 summary.*만: 착용자·날짜의 행을 지우고 새로 넣음(SM-3).

읽기는 기간(걷기 구간 시작 시각, 구간 시각)으로 먼저 좁힌 뒤 귀속 뷰를 씁니다(9절).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import psycopg

# 구간은 SEGMENT_MAX_S(기본 1시간)보다 길지 않음. 하루 앞까지 보면 그날에 걸친 구간을 모두 찾고, 시작 시각 색인을 탐.
SEGMENT_LOOKBACK = timedelta(days=1)


def subjects_on_device(conn: psycopg.Connection, device_id: str, start: datetime, end: datetime) -> list[int]:
    """SM-1: 그 장치에 [start, end)와 겹치는 착용 기록이 있는 착용자."""
    rows = conn.execute(
        """
        SELECT DISTINCT subject_id FROM registry.wear
        WHERE device_id = %s AND started_at < %s AND (ended_at IS NULL OR ended_at > %s)
        ORDER BY subject_id
        """,
        (device_id, end, start),
    ).fetchall()
    return [r["subject_id"] for r in rows]


def day_bouts(conn: psycopg.Connection, subject_id: int, start: datetime, end: datetime) -> list[dict]:
    """SM-2 (RP-2와 같은 고르기): 현재 run의 걷기 구간 중 시작 시각이 [start, end)이고 그 착용자에게 귀속된 것(SG-9),
    장치와 상관없이. formats는 그 run을 푼 기록의 형식(아직 풀리지 않았으면 NULL)."""
    return conn.execute(
        """
        SELECT b.id, b.run_id, b.start_at, b.end_at, b.mode, u.formats
        FROM analysis.bout b
        JOIN analysis.current_run cr ON cr.run_id = b.run_id
        JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        LEFT JOIN feature.unpack u ON u.run_id = b.run_id
        WHERE b.start_at >= %s AND b.start_at < %s AND bs.subject_id = %s
        ORDER BY b.start_at, b.id
        """,
        (start, end, subject_id),
    ).fetchall()


def records_of_bouts(conn: psycopg.Connection, bout_ids: Sequence[int]) -> list[dict]:
    """고른 걷기 구간의 줄. v는 낸 그대로이고 풀이는 metrics가 함(FT-4)."""
    return conn.execute(
        "SELECT bout_id, kind, side, v FROM feature.record WHERE bout_id = ANY(%s) ORDER BY bout_id, kind, idx",
        (list(bout_ids),),
    ).fetchall()


def wear_seconds(conn: psycopg.Connection, subject_id: int, start: datetime, end: datetime) -> float:
    """period.wear_time_s(RP-3 given): 그 착용자에게 귀속된 구간(SG-9)이 [start, end)와 겹친 시간의 합, 기간 안쪽만."""
    row = conn.execute(
        """
        SELECT coalesce(sum(extract(epoch FROM least(s.end_at, %(end)s) - greatest(s.start_at, %(start)s))), 0) AS s
        FROM segment.segment s
        JOIN analysis.segment_subject ss ON ss.segment_id = s.id
        WHERE s.device_id IN (SELECT device_id FROM registry.wear WHERE subject_id = %(subject)s)
          AND s.start_at >= %(lookback)s AND s.start_at < %(end)s AND s.end_at > %(start)s
          AND ss.subject_id = %(subject)s
        """,
        {"subject": subject_id, "start": start, "end": end, "lookback": start - SEGMENT_LOOKBACK},
    ).fetchone()
    return float(row["s"])


def delete_values(conn: psycopg.Connection, subject_id: int, day: date) -> None:
    conn.execute("DELETE FROM summary.daily_value WHERE subject_id = %s AND date = %s", (subject_id, day))


def delete_day(conn: psycopg.Connection, subject_id: int, day: date) -> None:
    conn.execute("DELETE FROM summary.day WHERE subject_id = %s AND date = %s", (subject_id, day))


def insert_day(conn: psycopg.Connection, subject_id: int, day: date, catalog_version: int, now: datetime) -> None:
    conn.execute(
        "INSERT INTO summary.day (subject_id, date, catalog_version, computed_at) VALUES (%s, %s, %s, %s)",
        (subject_id, day, catalog_version, now),
    )


_VALUE_COLS = ("subject_id", "date", "metric", "side", "value", "n", "sufficient")


def copy_values(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """rows: _VALUE_COLS 순서의 튜플. COPY 한 번."""
    if not rows:
        return
    with conn.cursor().copy(f"COPY summary.daily_value ({', '.join(_VALUE_COLS)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


# ---------------------------------------------------------------- 앱 조회 (14.8절)


def trend(conn: psycopg.Connection, subject_id: int, metric_ids: Sequence[str], first: date, last: date) -> list[dict]:
    """SM-5: 일별 값을 읽기만 함. 추세 조회의 색인 (subject_id, metric, side, date)를 탐. 날짜는 양 끝 포함."""
    return conn.execute(
        """
        SELECT metric, side, date, value, n, sufficient FROM summary.daily_value
        WHERE subject_id = %s AND metric = ANY(%s) AND date >= %s AND date <= %s
        ORDER BY metric, side NULLS FIRST, date
        """,
        (subject_id, list(metric_ids), first, last),
    ).fetchall()
