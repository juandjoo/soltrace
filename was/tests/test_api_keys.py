"""API 키 — 생성/검증 규칙과 조회 전용 강제.

DB 없이 동작하도록 세션과 쿼리를 최소한으로 흉내낸다.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api_keys
from app.deps import _principal_from_api_key


# ── 키 형식 / 해시 ──────────────────────────────────────────────────────────

def test_generated_keys_are_unique_and_prefixed():
    a, b = api_keys.generate_key(), api_keys.generate_key()
    assert a != b
    assert a.startswith(api_keys.KEY_PREFIX)
    assert len(a) == len(api_keys.KEY_PREFIX) + 48


def test_hash_is_stable_and_one_way():
    raw = api_keys.generate_key()
    h = api_keys.hash_key(raw)
    assert h == api_keys.hash_key(raw)
    assert len(h) == 64
    assert raw not in h


def test_prefix_is_short_and_not_enough_to_reconstruct():
    raw = api_keys.generate_key()
    p = api_keys.prefix_of(raw)
    assert raw.startswith(p) and len(p) < len(raw) / 2


def test_looks_like_key_separates_from_jwt():
    assert api_keys.looks_like_key(api_keys.generate_key())
    assert not api_keys.looks_like_key("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.x.y")


# ── resolve: 유효성 판정 ────────────────────────────────────────────────────

def key_row(**kw):
    base = dict(id=1, user_id=None, key_hash="", is_active=True,
                expires_at=None, last_used_at=None, key_prefix="slt_abc")
    base.update(kw)
    return SimpleNamespace(**base)


def user_row(**kw):
    base = dict(id=7, username="acme_view", role="customer", customer="ACME", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


class FakeDB:
    """ApiKey / User 조회를 흉내내는 최소 세션."""
    def __init__(self, key=None, user=None):
        self._key, self._user = key, user
        self.committed = False

    def query(self, model):
        target = self._key if model.__name__ == "ApiKey" else self._user
        return SimpleNamespace(filter=lambda *_a, **_k: SimpleNamespace(first=lambda: target))

    def commit(self):
        self.committed = True


def resolve(key=None, user=None, raw="slt_x"):
    return api_keys.resolve(FakeDB(key, user), raw)


def test_unknown_key_rejected():
    assert resolve(key=None) is None


def test_revoked_key_rejected():
    assert resolve(key=key_row(is_active=False)) is None


def test_expired_key_rejected():
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert resolve(key=key_row(expires_at=past)) is None


def test_future_expiry_accepted():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert resolve(key=key_row(expires_at=future)) is not None


def test_key_of_disabled_account_rejected():
    assert resolve(key=key_row(user_id=7), user=user_row(is_active=False)) is None
    assert resolve(key=key_row(user_id=7), user=None) is None


def test_admin_key_has_no_user():
    row, user = resolve(key=key_row(user_id=None))
    assert user is None and row is not None


# ── touch: 마지막 사용 기록은 자주 쓰지 않는다 ──────────────────────────────

def test_touch_writes_when_never_used():
    db, row = FakeDB(), key_row()
    api_keys.touch(db, row)
    assert db.committed and row.last_used_at is not None


def test_touch_skips_when_recent():
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    row = key_row(last_used_at=recent)
    api_keys.touch(db, row)
    assert not db.committed and row.last_used_at == recent


# ── 조회 전용 강제 + 권한 범위 ──────────────────────────────────────────────

class _AuthDB(FakeDB):
    """get_admin_username 이 읽는 app_config 조회까지 흉내낸다."""
    def execute(self, *_a, **_kw):
        return SimpleNamespace(first=lambda: ("root_admin",))


def principal(method, key=None, user=None):
    return _principal_from_api_key(_AuthDB(key, user), "slt_x", method)


def test_customer_key_is_scoped_to_its_customer():
    p = principal("GET", key_row(user_id=7), user_row())
    assert (p.role, p.customer, p.via_api_key) == ("customer", "ACME", True)
    assert not p.is_admin


def test_admin_key_grants_admin_scope():
    p = principal("GET", key_row(user_id=None))
    assert p.is_admin and p.username == "root_admin" and p.via_api_key


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_write_methods_are_rejected(method):
    with pytest.raises(HTTPException) as e:
        principal(method, key_row(user_id=None))
    assert e.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_read_methods_allowed(method):
    assert principal(method, key_row(user_id=None)).is_admin


def test_invalid_key_raises_401():
    with pytest.raises(HTTPException) as e:
        principal("GET", key=None)
    assert e.value.status_code == 401
