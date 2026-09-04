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


def _sql(f: LogFilters) -> str:
    """조건이 실제로 어떻게 SQL 로 붙는지 — DB 연결 없이 컴파일된 문자열만 본다."""
    from app.database import SessionLocal
    from app.models import FtpLog

    db = SessionLocal()
    return str(f.apply(db.query(FtpLog), db, None))


def test_cwd_probe_condition_is_parenthesized():
    """존재 확인 제외 조건의 OR 이 다른 필터를 삼키지 않아야 한다.

    괄호가 없으면 `기간 AND action<>'cwd_fail' OR NOT EXISTS(...)` 가 되어
    기간·장비·사용자 필터가 통째로 무력화된다(실제로 한 번 그렇게 났다).
    """
    sql = _sql(LogFilters(username="bob", log_status=None))
    assert "(ftp_logs.action <> 'cwd_fail'" in sql
    assert "OR NOT EXISTS" in sql
    # OR 절이 닫히고 난 뒤에 다른 조건이 AND 로 이어지는지 (괄호가 살아 있는지)
    or_pos = sql.index("OR NOT EXISTS")
    assert ")" in sql[or_pos:]


def test_cwd_probe_condition_applies_to_every_entry_point():
    """목록·총건수·CSV·XLSX 가 같은 LogFilters.apply 를 쓰므로 조건이 갈라지지 않는다."""
    import inspect

    from app.routers import logs as logs_router

    src = inspect.getsource(logs_router)
    assert src.count("_hide_cwd_probes") == 2          # 정의 1 + apply 안 호출 1
    assert "cwd_probe_sql" in src


def test_customer_and_usernames_filters():
    """고객사 + 계정 다중 선택이 SQL 로 붙는지 (조건이 조용히 빠지면 남의 데이터가 보인다)."""
    sql = _sql(LogFilters(customer="ACME", usernames="alice, bob", log_status=None))
    assert "groups.customer" in sql
    assert "ftp_logs.username IN" in sql


def test_blank_usernames_ignored():
    f = LogFilters(usernames=" , ,", log_status=None)
    assert f.usernames == []
