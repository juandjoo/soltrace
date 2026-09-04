"""로그인 계정 관리 — admin 전용.

관리자(admin)와 고객(customer) 계정을 모두 이 라우터에서 다룬다.
고객 계정의 격리 경계는 users.customer ↔ groups.customer 매칭으로 동작한다.

안전장치가 두 개 있다.
  - 마지막 활성 관리자는 비활성화·삭제할 수 없다 (아무도 못 들어오는 상태 방지).
  - 자기 자신은 비활성화·삭제할 수 없다 (실수로 스스로를 잠그는 것 방지).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, require_admin, validate_ip_entries
from app.models import User
from app.schemas import UserCreate, UserResponse, UserUpdate
from app.security import (
    get_admin_username, hash_password, lock_seconds_left, split_ips,
    strip_input as _clean, unlock_user,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _parse_ip_list(entries: List[str]) -> str:
    """IP/CIDR 목록 검증(deps.validate_ip_entries) 후 저장 포맷(줄바꿈 구분)으로 직렬화."""
    return "\n".join(validate_ip_entries(entries))


def _to_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id, username=u.username, role=u.role, customer=u.customer,
        allowed_ips=split_ips(u.allowed_ips), is_active=u.is_active,
        locked_seconds=lock_seconds_left(u), last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


def _get(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="계정을 찾을 수 없습니다")
    return user


def _assert_can_disable(db: Session, user: User, me: Principal) -> None:
    """비활성화/삭제 전 검사 — 자기 자신과 마지막 활성 관리자는 막는다."""
    if user.username == me.username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="자기 자신은 비활성화하거나 삭제할 수 없습니다")
    if user.role != "admin":
        return
    others = (
        db.query(User)
        .filter(User.role == "admin", User.is_active.is_(True), User.id != user.id)
        .count()
    )
    if others == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="마지막 활성 관리자입니다. 다른 관리자 계정을 먼저 추가·활성화하세요",
        )


@router.get("", response_model=List[UserResponse])
def list_users(db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    users = db.query(User).order_by(User.role, User.username).all()
    return [_to_response(u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _: Principal = Depends(require_admin)):
    username = _clean(body.username)
    customer = _clean(body.customer or "")
    role = body.role
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="아이디는 필수입니다")
    if role == "customer" and not customer:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="고객 계정은 고객사(customer)가 필요합니다")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자명입니다")
    # 부트스트랩 관리자와 같은 아이디는 users 이관 시 충돌한다
    if role != "admin" and username == get_admin_username(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="관리자 아이디와 동일한 사용자명은 사용할 수 없습니다")
    user = User(
        username=username,
        password_hash=hash_password(_clean(body.password)),
        role=role,
        customer=customer if role == "customer" else None,
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
    me: Principal = Depends(require_admin),
):
    user = _get(db, user_id)
    if body.password is not None:
        user.password_hash = hash_password(_clean(body.password))
        # 비밀번호를 새로 주면 잠금도 함께 푼다 (관리자가 대신 재설정해 주는 흐름)
        user.failed_attempts = 0
        user.locked_until = None
    if body.customer is not None:
        customer = _clean(body.customer)
        if user.role == "customer" and not customer:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="customer는 비울 수 없습니다")
        user.customer = customer or None
    if body.allowed_ips is not None:
        user.allowed_ips = _parse_ip_list(body.allowed_ips)
    if body.is_active is not None:
        if not body.is_active:
            _assert_can_disable(db, user, me)
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return _to_response(user)


@router.post("/{user_id}/unlock", response_model=UserResponse)
def unlock(user_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    """실패 횟수 초과로 잠긴 계정을 즉시 푼다 (기다리면 자동으로도 풀린다)."""
    user = _get(db, user_id)
    unlock_user(db, user)
    db.refresh(user)
    return _to_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db),
                me: Principal = Depends(require_admin)):
    user = _get(db, user_id)
    _assert_can_disable(db, user, me)
    db.delete(user)
    db.commit()
