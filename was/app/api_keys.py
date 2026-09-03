"""API 키 발급·검증 — 계정별 조회 전용 토큰.

설계
  - 키는 `slt_` + 48자리 난수(base62). 발급 시 한 번만 평문으로 보여주고 저장하지 않는다.
  - DB 에는 sha256 해시만 둔다. 키 자체가 고에너지 난수라 pbkdf2 같은 느린 해시가 필요 없고,
    매 API 요청마다 검증하므로 오히려 단일 sha256 이 맞다(사용자 비밀번호와는 성질이 다르다).
  - 조회 전용: GET/HEAD 이외의 요청은 deps 에서 차단한다. 권한 범위는 키 소유 계정과 같다
    (관리자 키 = 전체, 고객 계정 키 = 해당 customer 로 격리).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ApiKey, User

KEY_PREFIX = "slt_"
_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SECRET_LEN = 48
# 화면 식별용으로 남기는 앞부분 길이 (slt_ + 8자)
_DISPLAY_LEN = len(KEY_PREFIX) + 8
# last_used_at 을 매 요청마다 UPDATE 하지 않도록 하는 최소 간격
_TOUCH_INTERVAL = timedelta(minutes=1)


def generate_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_SECRET_LEN))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def prefix_of(raw: str) -> str:
    return raw[:_DISPLAY_LEN]


def looks_like_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX)


def create(db: Session, *, user_id: Optional[int], label: str,
           expires_at: Optional[datetime] = None) -> tuple[ApiKey, str]:
    """키를 발급한다. 반환값의 두 번째가 평문 키이며 이후 다시 볼 수 없다."""
    raw = generate_key()
    row = ApiKey(
        user_id=user_id,
        label=label or "",
        key_prefix=prefix_of(raw),
        key_hash=hash_key(raw),
        is_active=True,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def resolve(db: Session, raw: str) -> Optional[tuple[ApiKey, Optional[User]]]:
    """유효한 키면 (키, 소유 계정) 반환. 계정이 None 이면 관리자 키.

    비활성·만료된 키, 비활성 계정에 딸린 키는 None 을 돌려준다.
    """
    row = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(raw)).first()
    if not row or not row.is_active:
        return None
    if row.expires_at and row.expires_at <= datetime.now(timezone.utc):
        return None

    user = None
    if row.user_id is not None:
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user or not user.is_active:
            return None
    return row, user


def touch(db: Session, row: ApiKey) -> None:
    """마지막 사용 시각 갱신. 매 요청 쓰기를 피하려 1분 간격으로만 기록한다."""
    now = datetime.now(timezone.utc)
    if row.last_used_at and now - row.last_used_at < _TOUCH_INTERVAL:
        return
    row.last_used_at = now
    db.commit()
