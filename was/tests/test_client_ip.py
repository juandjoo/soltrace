"""접속 IP 판정 — 허용 IP 목록 우회 방지 회귀 테스트.

과거 결함: nginx 가 X-Forwarded-For 를 $proxy_add_x_forwarded_for 로 넘기면
클라이언트가 보낸 값 뒤에 실제 IP 가 덧붙는다. 앱이 첫 항목을 접속 IP 로 쓰면
헤더 한 줄로 관리자/고객사 계정의 허용 IP 목록을 통과할 수 있었다.
"""
from app.security import check_ip_allowed, check_user_ip_allowed, client_ip_from_request


class FakeRequest:
    def __init__(self, headers=None, peer="127.0.0.1"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": peer})() if peer else None


REAL = "203.0.113.99"          # 공격자의 실제 IP
INTERNAL = "10.20.30.40"       # 허용목록에 있는 사내 IP
ALLOWED = "10.20.30.0/24"


def test_forged_xff_does_not_override_real_ip():
    """위조 XFF 가 있어도 nginx 가 넣은 X-Real-IP 가 우선한다."""
    req = FakeRequest({"x-forwarded-for": f"{INTERNAL}, {REAL}", "x-real-ip": REAL})
    assert client_ip_from_request(req) == REAL


def test_forged_xff_cannot_pass_user_allowlist():
    req = FakeRequest({"x-forwarded-for": INTERNAL, "x-real-ip": REAL})
    assert not check_user_ip_allowed(ALLOWED, client_ip_from_request(req))


def test_genuine_internal_ip_passes():
    req = FakeRequest({"x-forwarded-for": INTERNAL, "x-real-ip": INTERNAL})
    assert check_user_ip_allowed(ALLOWED, client_ip_from_request(req))


def test_xff_used_only_without_real_ip():
    """X-Real-IP 가 없는 배치(직접 노출 등)에서는 XFF 첫 항목을 쓴다."""
    req = FakeRequest({"x-forwarded-for": f"{REAL}, 10.0.0.1"})
    assert client_ip_from_request(req) == REAL


def test_falls_back_to_socket_peer():
    assert client_ip_from_request(FakeRequest(peer="198.51.100.5")) == "198.51.100.5"
    assert client_ip_from_request(FakeRequest(peer=None)) == "0.0.0.0"


def test_header_whitespace_is_stripped():
    assert client_ip_from_request(FakeRequest({"x-real-ip": f"  {REAL}  "})) == REAL


class _DB:
    """check_ip_allowed 가 읽는 app_config 조회를 흉내낸다."""
    def __init__(self, value):
        self._v = value

    def execute(self, *_a, **_kw):
        v = self._v

        class _R:
            def first(self_inner):
                return (v,) if v is not None else None
        return _R()


def test_admin_office_allowlist_rejects_forged_header():
    db = _DB(ALLOWED)
    forged = FakeRequest({"x-forwarded-for": INTERNAL, "x-real-ip": REAL})
    assert not check_ip_allowed(db, client_ip_from_request(forged))
    genuine = FakeRequest({"x-real-ip": INTERNAL})
    assert check_ip_allowed(db, client_ip_from_request(genuine))


def test_empty_allowlist_allows_everyone():
    assert check_ip_allowed(_DB(""), REAL)
