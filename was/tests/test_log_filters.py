"""로그 조회 조건(LogFilters) — 목록/총건수/내보내기가 공유하는 단일 규칙."""
from datetime import datetime, timedelta, timezone

from app.routers.logs import DEFAULT_RANGE_DAYS, LogFilters


def test_default_range_applied_when_no_period():
    f = LogFilters(log_status=None)
    assert f.end_time is None
    assert f.start_time is not None
    expected = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)
    assert abs((f.start_time - expected).total_seconds()) < 5


def test_explicit_period_kept():
    s = datetime(2026, 1, 1, tzinfo=timezone.utc)
    e = datetime(2026, 1, 2, tzinfo=timezone.utc)
    f = LogFilters(start_time=s, end_time=e, log_status=None)
    assert (f.start_time, f.end_time) == (s, e)


def test_only_end_time_does_not_add_default_start():
    e = datetime(2026, 1, 2, tzinfo=timezone.utc)
    f = LogFilters(end_time=e, log_status=None)
    assert f.start_time is None and f.end_time == e
