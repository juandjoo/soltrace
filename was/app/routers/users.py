"""고객 계정 관리 — admin 전용.

admin 계정 자체는 app_config(설정 페이지)에서 관리하며 이 라우터는 role='customer'
계정만 다룬다. 격리 경계는 users.customer ↔ groups.customer 매칭으로 동작한다.
"""
import ipaddress
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import User
from app.schemas import UserCreate, UserResponse, UserUpdate
from app.security import get_admin_username, hash_password

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _clean(s: str) -> str:
    # 붙여넣기 인코딩 오류 방지: 공백/NBSP/탭 strip
    return s.replace(" ", " ").strip()


def _parse_ip_list(entries: List[str]) -> str:
    """IP/CIDR 목록 검증 후 줄바꿈 구분 문자열로 직렬화. 유효하지 않으면 422."""
    cleaned, invalid = [], []
    for e in entries:
        e = _clean(e) if isinstance(e, str) else str(e)
        if not e:
            continue
        try:
            ipaddress.ip_network(e, strict=False)
            cleaned.append(e)
        except ValueError:
            invalid.append(e)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"유효하지 않은 IP/CIDR: {', '.join(invalid)}",
        )
    return "\n".join(cleaned)


def _to_response(u: User) -> UserResponse:
    ips = [e.strip() for e in (u.allowed_ips or "").replace(",", "\n").splitlines() if e.strip()]
    return UserResponse(
        id=u.id, username=u.username, role=u.role, customer=u.customer,
        allowed_ips=ips, is_active=u.is_active, created_at=u.created_at,
    )


@router.get("", response_model=List[UserResponse])
def list_users(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    users = db.query(User).filter(User.role == "customer").order_by(User.username).all()
    return [_to_response(u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    username = _clean(body.username)
    customer = _clean(body.customer)
    if not username or not customer:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="username/customer는 필수입니다")
    if username == get_admin_username(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="관리자 아이디와 동일한 사용자명은 사용할 수 없습니다")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자명입니다")
    user = User(
        username=username,
        password_hash=hash_password(_clean(body.password)),
        role="customer",
        customer=customer,
        allowed_ips=_parse_ip_list(body.allowed_ips),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _to_response(user)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id, User.role == "customer").first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="계정을 찾을 수 없습니다")
    if body.password is not None:
        user.password_hash = hash_password(_clean(body.password))
    if body.customer is not None:
        customer = _clean(body.customer)
        if not customer:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="customer는 비울 수 없습니다")
        user.customer = customer
    if body.allowed_ips is not None:
        user.allowed_ips = _parse_ip_list(body.allowed_ips)
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return _to_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id, User.role == "customer").first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="계정을 찾을 수 없습니다")
    db.delete(user)
    db.commit()
