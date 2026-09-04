"""로그인 실패 잠금 — 5회 실패 시 15분 잠금, 성공/해제 시 초기화."""
from datetime import datetime, timedelta, timezone

from app import security
from app.models import User


class _CommitDB:
    """commit 만 세는 최소 세션 — 잠금 로직은 ORM 객체만 건드린다."""

    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _user(**kw):
    u = User(username="tester", password_hash="x", role="admin")
    u.failed_attempts = kw.get("failed_attempts", 0)
    u.locked_until = kw.get("locked_until")
    u.last_login_at = None
    return u


def test_locks_after_max_attempts():
    db, u = _CommitDB(), _user()
    remaining = [security.register_failed_login(db, u)
                 for _ in range(security.LOGIN_MAX_ATTEMPTS)]
    assert remaining == [4, 3, 2, 1, 0]
    assert u.locked_until is not None
    assert security.lock_seconds_left(u) > 0
    # 잠긴 뒤에는 카운터를 0부터 다시 센다
    assert u.failed_attempts == 0


def test_lock_expires_by_time():
    u = _user(locked_until=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert security.lock_seconds_left(u) == 0


def test_naive_locked_until_treated_as_utc():
    """DB 드라이버가 tz 없는 값을 주더라도 잠금 판정이 깨지지 않아야 한다."""
    naive = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(tzinfo=None)
    u = _user(locked_until=naive)
    assert security.lock_seconds_left(u) > 0


def test_success_clears_counter_and_lock():
    db, u = _CommitDB(), _user(failed_attempts=3,
                               locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
    security.clear_failed_login(db, u)
    assert u.failed_attempts == 0 and u.locked_until is None
    assert u.last_login_at is not None


def test_unlock_clears_lock():
    db, u = _CommitDB(), _user(failed_attempts=2,
                               locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
    security.unlock_user(db, u)
    assert u.failed_attempts == 0 and u.locked_until is None
    assert security.lock_seconds_left(u) == 0
