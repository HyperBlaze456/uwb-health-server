"""report의 SQL. 함수 하나 = 쿼리 하나. 지금은 읽기만 합니다(보고서 저장 RP-9는 아직 없음).

고르기는 summary의 하루 고르기(SM-2)와 같은 규칙을 임의의 기간 [start, end)에 씁니다(RP-2). 단계끼리 import하지 않으므로
SQL은 여기 따로 둡니다. 기간(걷기 구간 시작 시각, 구간 시각)으로 먼저 좁힌 뒤 귀속 뷰를 씁니다(9절).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import psycopg

# 구간은 SEGMENT_MAX_S(기본 1시간)보다 길지 않음. 하루 앞까지 보면 기간에 걸친 구간을 모두 찾고, 시작 시각 색인을 탐.
SEGMENT_LOOKBACK = timedelta(days=1)


def subject(conn: psycopg.Connection, subject_id: int) -> dict | None:
    """RP-7 subject: 착용자 코드, 키, 다리 길이, 마비측. 실명은 이 표에 없음."""
    return conn.execute(
        "SELECT id, code, height_m, leg_length_m, paretic_side FROM registry.subject WHERE id = %s", (subject_id,)
    ).fetchone()


def period_bouts(conn: psycopg.Connection, subject_id: int, start: datetime, end: datetime) -> list[dict]:
    """RP-2: 현재 run의 걷기 구간 중 시작 시각이 [start, end)이고 그 착용자에게 귀속된 것(SG-9), 장치와 상관없이.
    경계에 걸친 걷기 구간은 시작한 쪽에만 듦. formats는 그 run을 푼 기록의 형식(아직 풀리지 않았으면 NULL),
    result_status는 run의 complete·partial."""
    return conn.execute(
        """
        SELECT b.id, b.run_id, b.start_at, b.end_at, b.mode, b.sides, u.formats, r.result_status
        FROM analysis.bout b
        JOIN analysis.current_run cr ON cr.run_id = b.run_id
        JOIN analysis.run r ON r.id = b.run_id
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


def ambiguous_bouts(conn: psycopg.Connection, subject_id: int, start: datetime, end: datetime) -> int:
    """RP-7 coverage: 기간에 시작한 현재 run의 걷기 구간 중 그 착용자의 착용 기록이 걸리지만 주인이 모호해(같은 우선순위에
    둘 이상) 뺀 것의 수."""
    row = conn.execute(
        """
        SELECT count(*) AS n
        FROM analysis.bout b
        JOIN analysis.current_run cr ON cr.run_id = b.run_id
        JOIN analysis.run r ON r.id = b.run_id
        JOIN segment.segment s ON s.id = r.segment_id
        JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        WHERE b.start_at >= %(start)s AND b.start_at < %(end)s AND bs.ambiguous
          AND EXISTS (SELECT 1 FROM registry.wear w
                      WHERE w.subject_id = %(subject)s AND w.device_id = s.device_id
                        AND w.started_at <= b.start_at AND (w.ended_at IS NULL OR b.start_at < w.ended_at))
        """,
        {"subject": subject_id, "start": start, "end": end},
    ).fetchone()
    return int(row["n"])


def daily_values(conn: psycopg.Connection, subject_id: int, first: date, last: date) -> list[dict]:
    """RP-4: 일별 값(양 끝 포함). 견주기 계산은 rules.baseline이 함."""
    return conn.execute(
        """
        SELECT date, metric, side, value, n, sufficient FROM summary.daily_value
        WHERE subject_id = %s AND date >= %s AND date <= %s
        ORDER BY metric, side NULLS FIRST, date
        """,
        (subject_id, first, last),
    ).fetchall()
