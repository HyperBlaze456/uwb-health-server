"""DB 행 → BoutData (metrics.bouts_from_rows)와 한국 날짜(clock.local_date, day_start). DB 없는 순수 시험.

일별 값(SM-3)과 보고서(RP-2)가 같은 걷기 구간에서 같은 숫자를 얻으려면, 행을 BoutData로 바꾸는 일도 한 곳이어야 합니다.
"""

from datetime import UTC, date, datetime, timedelta

from uwbserver.pipeline import clock, metrics
from uwbserver.pipeline.metrics import BoutData, Record

V1 = "gaitfusion.stride/1"
T = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _bout(id_, run_id, seconds, mode="fused"):
    return {"id": id_, "run_id": run_id, "start_at": T, "end_at": T + timedelta(seconds=seconds), "mode": mode}


def _rec(bout_id, side, **v):
    return {"bout_id": bout_id, "kind": "stride", "side": side, "v": {"side": side, "start_s": 1.0, "end_s": 2.0, **v}}


def test_rows_become_normalized_bout_data():
    cat = metrics.load()
    bouts = [_bout(1, 10, 42.5), _bout(2, 11, 12.0, "imu_only")]
    records = [
        _rec(1, "left", duration_s=1.1, length_m=1.2),
        _rec(1, "right", duration_s=1.0, length_m=99.0),  # 범위 밖 → 값 없음(normalize)
        _rec(2, "left", duration_s=0.9),
        {"bout_id": 2, "kind": "gesture", "side": None, "v": {"x": 1}},  # 목록에 표가 없는 종류 → 버림
    ]
    formats = {10: {"strides": V1}, 11: {}}  # 11은 형식을 밝히지 않은 지난 응답(MC-3: /1로 봄)
    got = metrics.bouts_from_rows(cat, bouts, records, formats)
    assert got == [
        BoutData(42.5, "fused", {"stride": (Record("left", {"stride.duration_s": 1.1, "stride.length_m": 1.2}),
                                            Record("right", {"stride.duration_s": 1.0}))}),
        BoutData(12.0, "imu_only", {"stride": (Record("left", {"stride.duration_s": 0.9}),)}),
    ]  # fmt: skip


def test_unpacked_run_missing_or_unknown_format_gives_no_records():
    """풀리지 않은 run(formats 없음)과 목록이 모르는 판의 줄은 풀이하지 않음. 걷기 구간 자체는 남음(횟수·시간에 셈)."""
    cat = metrics.load()
    bouts = [_bout(1, 10, 30.0), _bout(2, 11, 20.0)]
    records = [_rec(1, "left", duration_s=1.1), _rec(2, "left", duration_s=1.1)]
    got = metrics.bouts_from_rows(cat, bouts, records, {10: None, 11: {"strides": "gaitfusion.stride/99"}})
    assert got == [BoutData(30.0, "fused", {}), BoutData(20.0, "fused", {"stride": (Record("left", {}),)})]


def test_period_values_over_rows_equal_hand_built_bouts():
    cat = metrics.load()
    bouts = [_bout(1, 10, 45.0)]
    records = [_rec(1, "left", duration_s=1.0 + 0.01 * i, speed_mps=1.2) for i in range(5)]
    by_rows = metrics.period_values(cat, metrics.bouts_from_rows(cat, bouts, records, {10: {}}), wear_time_s=60.0)
    by_hand = metrics.period_values(
        cat,
        [BoutData(45.0, "fused", {"stride": tuple(Record("left", {"stride.duration_s": 1.0 + 0.01 * i,
                                                                   "stride.speed_mps": 1.2}) for i in range(5))})],
        wear_time_s=60.0,
    )  # fmt: skip
    assert by_rows == by_hand


# --- 한국 날짜 (SM-2) -------------------------------------------------------------------


def test_local_date_is_korean_calendar_date():
    assert clock.local_date(datetime(2026, 10, 25, 14, 59, 59, tzinfo=UTC)) == date(2026, 10, 25)
    assert clock.local_date(datetime(2026, 10, 25, 15, 0, tzinfo=UTC)) == date(2026, 10, 26)  # 한국 자정
    assert clock.local_date(datetime(2026, 10, 25, 23, 59, tzinfo=UTC)) == date(2026, 10, 26)  # UTC 자정 근처
    assert clock.local_date(datetime(2026, 10, 26, 0, 1, tzinfo=UTC)) == date(2026, 10, 26)


def test_day_start_is_korean_midnight():
    assert clock.day_start(date(2026, 10, 26)) == datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
    assert clock.day_start(date(2026, 7, 1)) == datetime(2026, 6, 30, 15, 0, tzinfo=UTC)  # 여름에도 같음(일광 절약 없음)


def test_today_follows_the_clock():
    clock.use(clock.FakeClock(datetime(2026, 10, 25, 15, 30, tzinfo=UTC)))
    try:
        assert clock.today() == date(2026, 10, 26)
    finally:
        clock.use(None)
