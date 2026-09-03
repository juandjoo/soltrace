"""고객사 계정 격리·인증 핵심 로직 단위 테스트 (DB 불필요).

보안 경계에 해당하는 순수 함수만 다룬다:
  - 비밀번호 해시/검증, IP·CIDR 허용 검사
  - JWT role/customer 클레임 왕복, 구버전 토큰 fallback
  - require_admin / device_scope 격리 판정
"""
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from app import deps, security
from app.config import settings


# ── 비밀번호 ────────────────────────────────────────────────────────────────

def test_password_roundtrip():
    h = security.hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert security.verify_password("s3cret!", h)
    assert not security.verify_password("wrong", h)


def test_verify_password_rejects_garbage():
    assert not security.verify_password("x", "not-a-hash")
    assert not security.verify_password("x", "")
    assert not security.verify_password("x", None)


# ── IP 허용 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("client,entry,ok", [
    ("10.0.0.5", "10.0.0.0/8", True),
    ("10.0.0.5", "10.0.0.5", True),
    ("10.0.0.5", "10.0.0.6", False),
    ("192.168.1.9", "192.168.1.0/24", True),
    ("192.168.2.9", "192.168.1.0/24", False),
    ("::1", "::1", True),
])
def test_ip_matches(client, entry, ok):
    assert security._ip_matches(client, entry) is ok


def test_user_ip_allowed_empty_means_all():
    assert security.check_user_ip_allowed(None, "8.8.8.8")
    assert security.check_user_ip_allowed("", "8.8.8.8")
    assert security.check_user_ip_allowed("  \n , ", "8.8.8.8")


def test_user_ip_allowed_list_formats():
    raw = "10.0.0.0/8, 203.0.113.7\n198.51.100.0/24"
    assert security.check_user_ip_allowed(raw, "10.9.9.9")
    assert security.check_user_ip_allowed(raw, "203.0.113.7")
    assert security.check_user_ip_allowed(raw, "198.51.100.200")
    assert not security.check_user_ip_allowed(raw, "203.0.113.8")


# ── JWT / Principal ────────────────────────────────────────────────────────

class _NoHeaderRequest:
    """API 키 헤더가 없는 요청 — JWT 경로만 타게 한다."""
    headers: dict = {}
    method = "GET"


def _principal_from(token: str) -> deps.Principal:
    return deps.get_current_user(
        _NoHeaderRequest(),
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        db=None,
    )


def test_customer_token_roundtrip():
    tok = deps.create_access_token("acme_user", "customer", "ACME")
    p = _principal_from(tok)
    assert (p.username, p.role, p.customer) == ("acme_user", "customer", "ACME")
    assert not p.is_admin


def test_admin_token_has_no_customer():
    p = _principal_from(deps.create_access_token("admin", "admin"))
    assert p.is_admin and p.customer is None


def test_legacy_token_without_role():
    # 구버전 토큰(role 클레임 없음): sub='admin' 만 admin, 그 외는 customer 로 강등
    legacy_admin = jwt.encode({"sub": "admin"}, settings.secret_key, algorithm="HS256")
    legacy_other = jwt.encode({"sub": "someone"}, settings.secret_key, algorithm="HS256")
    assert _principal_from(legacy_admin).is_admin
    assert not _principal_from(legacy_other).is_admin


def test_tampered_token_rejected():
    tok = deps.create_access_token("acme_user", "customer", "ACME")
    forged = jwt.encode({"sub": "acme_user", "role": "admin"}, "other-key", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        _principal_from(forged)
    assert e.value.status_code == 401
    assert _principal_from(tok).role == "customer"


def test_require_admin_blocks_customer():
    cust = deps.Principal(username="c", role="customer", customer="ACME")
    with pytest.raises(HTTPException) as e:
        deps.require_admin(cust)
    assert e.value.status_code == 403
    assert deps.require_admin(deps.Principal(username="admin", role="admin")).is_admin


# ── device_scope 격리 ──────────────────────────────────────────────────────

class _FakeDB:
    """device_scope 가 실행하는 SQL 의 바인딩 값을 기록하고 고정 결과를 돌려준다."""
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def execute(self, stmt, params=None):
        self.params = params
        rows = self.rows

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_s): return list(rows)
                return _S()
        return _R()


def test_device_scope_admin_is_unrestricted():
    db = _FakeDB([1, 2, 3])
    assert deps.device_scope(deps.Principal(username="admin", role="admin"), db) is None
    assert db.params is None  # 쿼리 자체를 실행하지 않음


def test_device_scope_customer_filters_by_customer():
    db = _FakeDB([7, 9])
    scope = deps.device_scope(deps.Principal(username="c", role="customer", customer="ACME"), db)
    assert scope == [7, 9]
    assert db.params == {"c": "ACME"}


def test_device_scope_customer_without_customer_sees_nothing():
    db = _FakeDB([])
    scope = deps.device_scope(deps.Principal(username="c", role="customer", customer=None), db)
    assert scope == []            # 빈 리스트 → 조회 시 IN () 로 아무것도 안 보임
    assert db.params == {"c": ""}


# ── 공용 입력 헬퍼 (settings 전역 IP / users 계정별 IP 가 같은 규칙) ────────

def test_strip_input_removes_nbsp_and_whitespace():
    assert security.strip_input("  10.0.0.1\t ") == "10.0.0.1"


def test_parse_ip_entries_splits_valid_invalid():
    valid, invalid = security.parse_ip_entries(["10.0.0.0/8", " 203.0.113.7 ", "", "not-an-ip", 42])
    assert valid == ["10.0.0.0/8", "203.0.113.7"]
    assert invalid == ["not-an-ip", "42"]


def test_split_ips_accepts_comma_and_newline():
    assert security.split_ips("a, b\n\nc ,") == ["a", "b", "c"]
    assert security.split_ips(None) == []
