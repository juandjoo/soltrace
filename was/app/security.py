"""비밀번호 해시 및 전역 설정(app_config) 접근 헬퍼.

비밀번호는 표준 라이브러리(hashlib.pbkdf2_hmac)만으로 해시한다 — 추가 의존성 없음.
저장 포맷: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

import hashlib
import hmac
import ipaddress
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models import User

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


def strip_input(s: str) -> str:
    """붙여넣기 인코딩 오류 방지: NBSP(U+00A0)→공백 치환 후 앞뒤 공백/탭 제거."""
    return s.replace("\u00a0", " ").strip()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── app_config 키-값 접근 ────────────────────────────────────────────────────

def get_config(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.execute(
        text("SELECT value FROM app_config WHERE key = :k"), {"k": key}
    ).first()
    return row[0] if row else default


def set_config(db: Session, key: str, value: str) -> None:
    db.execute(
        text(
            "INSERT INTO app_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ),
        {"k": key, "v": value},
    )
    db.commit()


# ── 관리자 아이디 / 비밀번호 ─────────────────────────────────────────────────
# DB에 값이 있으면 그것으로 검증, 없으면 .env 기본값(admin / ADMIN_PASSWORD)으로 부트스트랩.

ADMIN_PW_KEY  = "admin_password_hash"
ADMIN_ID_KEY  = "admin_username"
ALLOWED_IPS_KEY = "allowed_ips"         # 레거시 키 (마이그레이션 호환)
OFFICE_IPS_KEY  = "allowed_ips_office"  # 웹 접속 허용 IP/CIDR


def get_admin_username(db: Session) -> str:
    return get_config(db, ADMIN_ID_KEY) or app_settings.admin_username


def set_admin_username(db: Session, username: str) -> None:
    set_config(db, ADMIN_ID_KEY, username)


def verify_admin_password(db: Session, password: str) -> bool:
    stored = get_config(db, ADMIN_PW_KEY)
    if stored:
        return verify_password(password, stored)
    return hmac.compare_digest(password, app_settings.admin_password)


def verify_admin_credentials(db: Session, username: str, password: str) -> bool:
    # 두 검증을 항상 실행해 타이밍 사이드채널 방지
    username_ok = hmac.compare_digest(username, get_admin_username(db))
    password_ok = verify_admin_password(db, password)
    return username_ok and password_ok


def set_admin_password(db: Session, new_password: str) -> None:
    set_config(db, ADMIN_PW_KEY, hash_password(new_password))


# ── 접속 IP 허용 목록 ────────────────────────────────────────────────────────

def split_ips(raw: str | None) -> list[str]:
    """저장 포맷(줄바꿈/쉼표 구분 문자열) → 항목 리스트. 빈 항목 제거."""
    return [e.strip() for e in (raw or "").replace(",", "\n").splitlines() if e.strip()]


def parse_ip_entries(entries: list) -> tuple[list[str], list[str]]:
    """사용자 입력 IP/CIDR 목록 검증. (유효 항목, 무효 항목) 반환.

    admin 전역 허용 IP(settings)와 고객 계정별 허용 IP(users)가 같은 규칙을 쓴다.
    빈 항목은 무시하고, 문자열이 아닌 값은 무효로 본다.
    """
    valid, invalid = [], []
    for e in entries:
        if not isinstance(e, str):
            invalid.append(str(e))
            continue
        e = strip_input(e)
        if not e:
            continue
        try:
            ipaddress.ip_network(e, strict=False)
            valid.append(e)
        except ValueError:
            invalid.append(e)
    return valid, invalid


def get_office_ips(db: Session) -> list[str]:
    # None = 키 자체가 DB에 없음 → 레거시 마이그레이션
    # ""   = 키가 있지만 비어있음 → 사용자가 의도적으로 초기화한 것
    raw = get_config(db, OFFICE_IPS_KEY)
    if raw is None:
        return split_ips(get_config(db, ALLOWED_IPS_KEY))
    return split_ips(raw)


def set_office_ips(db: Session, ips: list[str]) -> None:
    set_config(db, OFFICE_IPS_KEY, "\n".join(ip.strip() for ip in ips if ip.strip()))


def _ip_matches(client_ip: str, entry: str) -> bool:
    """entry는 단일 IP 또는 CIDR 표기 (예: 10.0.0.0/8)."""
    try:
        addr = ipaddress.ip_address(client_ip)
        net = ipaddress.ip_network(entry, strict=False)
        return addr in net
    except ValueError:
        return client_ip == entry


def check_ip_allowed(db: Session, client_ip: str) -> bool:
    allowed = get_office_ips(db)
    if not allowed:
        return True  # 목록 미설정 시 모두 허용
    return any(_ip_matches(client_ip, entry) for entry in allowed)


# ── 고객 계정(users) ─────────────────────────────────────────────────────────
# 더미 해시: 존재하지 않는 사용자에 대해서도 항상 검증을 수행해 타이밍 차이로
# 계정 존재 여부가 새지 않게 한다.
_DUMMY_HASH = hash_password("soltrace-nonexistent-account")


def get_active_user(db: Session, username: str) -> User | None:
    """username 으로 활성 사용자(User) 조회. 없거나 비활성이면 None."""
    return (
        db.query(User)
        .filter(User.username == username, User.is_active.is_(True))
        .first()
    )


def check_user_ip_allowed(allowed_ips_raw: str | None, client_ip: str) -> bool:
    """계정별 허용 IP 검사. 목록이 비어있으면 모두 허용."""
    entries = split_ips(allowed_ips_raw)
    if not entries:
        return True
    return any(_ip_matches(client_ip, e) for e in entries)


def client_ip_from_request(request) -> str:
    """접속 IP 판정. 리버스 프록시(nginx) 뒤에서는 client.host 가 127.0.0.1 이라 헤더를 본다.

    우선순위: X-Real-IP → X-Forwarded-For(첫 항목) → client.host

    X-Real-IP 를 먼저 보는 이유: nginx 는 이 헤더를 항상 $remote_addr 로 **덮어쓰므로**
    클라이언트가 위조할 수 없다. 반면 X-Forwarded-For 를 $proxy_add_x_forwarded_for 로
    넘기면 클라이언트가 보낸 값 뒤에 실제 IP 가 덧붙어, 첫 항목이 공격자 값이 된다
    (허용 IP 목록 우회). nginx.conf 도 XFF 를 $remote_addr 로 덮어쓰도록 맞춰져 있으며,
    설정이 어긋나더라도 여기서 한 번 더 막는다.
    """
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"
