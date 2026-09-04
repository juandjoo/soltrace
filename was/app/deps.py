from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import api_keys
from app.config import settings
from app.database import get_db
from app.models import User
from app.security import get_admin_username

# auto_error=False: Authorization 헤더가 없어도 여기서 바로 401 을 내지 않는다
# (X-API-Key 헤더로 인증하는 경로가 있으므로 판단을 get_current_user 로 미룬다).
bearer = HTTPBearer(auto_error=False)

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class Principal:
    """로그인한 주체. role='admin'은 전체 접근, 'customer'는 customer 단위 격리."""
    username: str
    role: str
    customer: Optional[str] = None
    via_api_key: bool = False      # API 키로 인증한 요청 (조회 전용)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def create_access_token(username: str = "admin", role: str = "admin",
                        customer: Optional[str] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expire}
    if customer is not None:
        payload["customer"] = customer
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _principal_from_api_key(db: Session, raw: str, method: str) -> Principal:
    found = api_keys.resolve(db, raw)
    if not found:
        raise _unauthorized("Invalid or expired API key")
    row, user = found
    # API 키는 조회 전용 — 변경 요청은 키 유효 여부와 무관하게 차단
    if method.upper() not in _READ_METHODS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API 키는 조회(GET)만 허용됩니다",
        )
    api_keys.touch(db, row)
    if user is None:
        return Principal(username=get_admin_username(db), role="admin", via_api_key=True)
    return Principal(username=user.username, role=user.role,
                     customer=user.customer, via_api_key=True)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    """웹 세션(JWT) 또는 API 키로 인증한다.

    API 키는 `X-API-Key: slt_...` 헤더, 또는 `Authorization: Bearer slt_...` 로도 받는다
    (키가 고정 접두어로 시작하므로 JWT 와 구분된다).
    """
    raw_key = request.headers.get("x-api-key")
    if not raw_key and credentials and api_keys.looks_like_key(credentials.credentials):
        raw_key = credentials.credentials
    if raw_key:
        return _principal_from_api_key(db, raw_key.strip(), request.method)

    if not credentials:
        raise _unauthorized("Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        raise _unauthorized()
    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Invalid token")
    # role 누락 토큰(구버전 admin 토큰 sub='admin')은 admin 으로 취급
    role = payload.get("role") or ("admin" if sub == "admin" else "customer")
    customer = payload.get("customer")
    # 계정 상태를 매 요청 확인한다 — 비활성화가 이미 발급된 토큰에도 바로 먹어야 한다.
    # (users 에 행이 없는 부트스트랩 관리자는 토큰 값을 그대로 쓴다)
    row = db.query(User).filter(User.username == sub).first()
    if row is not None:
        if not row.is_active:
            raise _unauthorized("비활성화된 계정입니다")
        role, customer = row.role, row.customer
    return Principal(username=sub, role=role, customer=customer)


def require_admin(user: Principal = Depends(get_current_user)) -> Principal:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def device_scope(
    user: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Optional[list[int]]:
    """조회 격리용 허용 device_id 목록.

    - admin            → None (필터 없음, 전체 접근)
    - customer         → 본인 customer 의 groups 에 속한 device_id 목록 (없으면 빈 리스트 → 아무것도 못 봄)
    """
    if user.is_admin:
        return None
    rows = db.execute(
        text(
            "SELECT DISTINCT dg.device_id "
            "FROM device_groups dg JOIN groups g ON g.id = dg.group_id "
            "WHERE g.customer = :c"
        ),
        {"c": user.customer or ""},
    ).scalars().all()
    return list(rows)
