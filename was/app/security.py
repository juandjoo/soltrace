"""비밀번호 해시 및 전역 설정(app_config) 접근 헬퍼.

비밀번호는 표준 라이브러리(hashlib.pbkdf2_hmac)만으로 해시한다 — 추가 의존성 없음.
저장 포맷: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

import hashlib
import hmac
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


# ── 관리자 비밀번호 ──────────────────────────────────────────────────────────
# DB에 해시가 있으면 그것으로 검증, 없으면 .env의 ADMIN_PASSWORD로 부트스트랩.

ADMIN_PW_KEY = "admin_password_hash"


def verify_admin_password(db: Session, password: str) -> bool:
    stored = get_config(db, ADMIN_PW_KEY)
    if stored:
        return verify_password(password, stored)
    return hmac.compare_digest(password, app_settings.admin_password)


def set_admin_password(db: Session, new_password: str) -> None:
    set_config(db, ADMIN_PW_KEY, hash_password(new_password))
