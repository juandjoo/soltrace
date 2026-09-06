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
from app.models import Group, User, UserFtpAccount
from app.schemas import FtpAccountMap, UserCreate, UserResponse, UserUpdate
from app.security import (
    get_admin_username, hash_password, lock_seconds_left, split_ips,
    strip_input as _clean, unlock_user,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _parse_ip_list(entries: List[str]) -> str:
    """IP/CIDR 목록 검증(deps.validate_ip_entries) 후 저장 포맷(줄바꿈 구분)으로 직렬화."""
    return "\n".join(validate_ip_entries(entries))


# FTP 계정 매핑은 한 계정에 이만큼까지만 — 실수로 붙여넣은 수천 줄이 들어오는 것을 막는다
_MAX_FTP_ACCOUNTS = 500


def _load_ftp_map(db: Session, user_id: int) -> list:
    """이 계정의 (그룹, FTP 아이디) 매핑을 그룹 단위로 묶어 돌려준다."""
    rows = (
        db.query(UserFtpAccount.group_id, UserFtpAccount.ftp_username, Group.name)
        .join(Group, Group.id == UserFtpAccount.group_id)
        .filter(UserFtpAccount.user_id == user_id)
        .order_by(Group.name, UserFtpAccount.ftp_username)
        .all()
    )
    out: dict = {}
    for gid, ftp_username, gname in rows:
        item = out.setdefault(gid, FtpAccountMap(group_id=gid, group_name=gname, usernames=[]))
        item.usernames.append(ftp_username)
    return list(out.values())


def _save_ftp_map(db: Session, user: User, entries: list) -> None:
    """매핑을 통째로 교체한다 (관리자 화면이 전체 목록을 보내는 방식).

    그룹이 그 계정의 고객사(users.customer ↔ groups.customer) 것인지 확인한다 —
    조회 시에도 같은 조건을 다시 보지만, 애초에 잘못된 조합이 저장되지 않게 막는다.
    """
    if user.role != "customer":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="FTP 계정 매핑은 고객 계정에만 설정할 수 있습니다")
    pairs: list[tuple[int, str]] = []
    seen: set = set()
    for e in entries or []:
        group = db.query(Group).filter(Group.id == e.group_id).first()
        if not group:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"그룹을 찾을 수 없습니다 (id={e.group_id})")
        if (group.customer or "") != (user.customer or ""):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{group.name}' 은 이 계정의 고객사({user.customer}) 그룹이 아닙니다")
        for raw in e.usernames or []:
            name = _clean(raw)
            if not name:
                continue
            if len(name) > 255:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="FTP 아이디가 너무 깁니다 (255자 초과)")
            key = (group.id, name)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    if len(pairs) > _MAX_FTP_ACCOUNTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"FTP 계정은 최대 {_MAX_FTP_ACCOUNTS}개까지 등록할 수 있습니다")

    db.query(UserFtpAccount).filter(UserFtpAccount.user_id == user.id).delete()
    for gid, name in pairs:
        db.add(UserFtpAccount(user_id=user.id, group_id=gid, ftp_username=name))


def _to_response(db: Session, u: User) -> UserResponse:
    return UserResponse(
        id=u.id, username=u.username, role=u.role, customer=u.customer,
        allowed_ips=split_ips(u.allowed_ips), is_active=u.is_active,
        ftp_accounts=_load_ftp_map(db, u.id) if u.role == "customer" else [],
        note=u.note, created_by=u.created_by,
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
    return [_to_response(db, u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                me: Principal = Depends(require_admin)):
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
        note=_clean(body.note or "") or None,
        created_by=me.username,          # 누가 만든 계정인지 목록에 그대로 보인다
    )
    db.add(user)
    db.flush()                      # user.id 확보 (매핑이 이 id 를 참조한다)
    if role == "customer":
        _save_ftp_map(db, user, body.ftp_accounts)
    db.commit()
    db.refresh(user)
    return _to_response(db, user)


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
    if body.note is not None:
        user.note = _clean(body.note) or None
    if body.is_active is not None:
        if not body.is_active:
            _assert_can_disable(db, user, me)
        user.is_active = body.is_active
    if body.ftp_accounts is not None:
        _save_ftp_map(db, user, body.ftp_accounts)
    db.commit()
    db.refresh(user)
    return _to_response(db, user)


@router.post("/{user_id}/unlock", response_model=UserResponse)
def unlock(user_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    """실패 횟수 초과로 잠긴 계정을 즉시 푼다 (기다리면 자동으로도 풀린다)."""
    user = _get(db, user_id)
    unlock_user(db, user)
    db.refresh(user)
    return _to_response(db, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db),
                me: Principal = Depends(require_admin)):
    user = _get(db, user_id)
    _assert_can_disable(db, user, me)
    db.delete(user)
    db.commit()
