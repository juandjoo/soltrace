from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Device

bearer = HTTPBearer()


@dataclass
class Principal:
    """로그인한 주체. role='admin'은 전체 접근, 'customer'는 customer 단위 격리."""
    username: str
    role: str
    customer: Optional[str] = None

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


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> Principal:
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    # role 누락 토큰(구버전 admin 토큰 sub='admin')은 admin 으로 취급
    role = payload.get("role") or ("admin" if sub == "admin" else "customer")
    return Principal(username=sub, role=role, customer=payload.get("customer"))


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


def require_device(device_key: str, db: Session = Depends(get_db)) -> Device:
    device = db.query(Device).filter(Device.device_key == device_key).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown device key")
    if device.status == "disabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is disabled")
    return device
