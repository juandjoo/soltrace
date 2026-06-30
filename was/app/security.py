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

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


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

def _parse_ips(raw: str) -> list[str]:
    return [e.strip() for e in raw.replace(",", "\n").splitlines() if e.strip()]


def get_office_ips(db: Session) -> list[str]:
    # None = 키 자체가 DB에 없음 → 레거시 마이그레이션
    # ""   = 키가 있지만 비어있음 → 사용자가 의도적으로 초기화한 것
    raw = get_config(db, OFFICE_IPS_KEY)
    if raw is None:
        legacy = get_config(db, ALLOWED_IPS_KEY) or ""
        return _parse_ips(legacy)
    return _parse_ips(raw)


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


def get_active_user(db: Session, username: str):
    """username 으로 활성 사용자(User) 조회. 없거나 비활성이면 None."""
    from app.models import User  # 순환 import 방지
    return (
        db.query(User)
        .filter(User.username == username, User.is_active.is_(True))
        .first()
    )


def check_user_ip_allowed(allowed_ips_raw: str | None, client_ip: str) -> bool:
    """계정별 허용 IP 검사. 목록이 비어있으면 모두 허용."""
    entries = _parse_ips(allowed_ips_raw or "")
    if not entries:
        return True
    return any(_ip_matches(client_ip, e) for e in entries)


def client_ip_from_request(request) -> str:
    """리버스 프록시(nginx) 뒤에서는 client.host 가 127.0.0.1 이므로 XFF 우선 참조.

    settings.get_security 와 동일한 우선순위: X-Forwarded-For → X-Real-IP → client.host.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "0.0.0.0"
    )
