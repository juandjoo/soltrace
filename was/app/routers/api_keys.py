"""API 키 관리 — admin 전용.

키 자체는 조회(GET) 전용 토큰이며, 권한 범위는 소유 계정과 같다.
  - 관리자 키(user_id 없음) : 전체 조회
  - 고객 계정 키            : 해당 customer 의 장비 데이터만 (device_scope 그대로 적용)

발급 응답에만 평문 키가 들어간다. 저장하지 않으므로 다시 확인할 수 없고, 분실하면 재발급한다.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import api_keys as keys
from app.database import get_db
from app.deps import require_admin
from app.models import ApiKey, User
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyResponse
from app.security import get_admin_username, strip_input

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _to_response(row: ApiKey, user: Optional[User], admin_username: str) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        user_id=row.user_id,
        username=user.username if user else admin_username,
        role=user.role if user else "admin",
        customer=user.customer if user else None,
        label=row.label or "",
        key_prefix=row.key_prefix,
        is_active=row.is_active,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.get("", response_model=List[ApiKeyResponse])
def list_keys(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    admin_username = get_admin_username(db)
    rows = (
        db.query(ApiKey, User)
        .outerjoin(User, User.id == ApiKey.user_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [_to_response(row, user, admin_username) for row, user in rows]


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(body: ApiKeyCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    user = None
    if body.user_id is not None:
        user = db.query(User).filter(User.id == body.user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="계정을 찾을 수 없습니다")
    row, raw = keys.create(
        db,
        user_id=body.user_id,
        label=strip_input(body.label or ""),
        expires_at=body.expires_at,
    )
    base = _to_response(row, user, get_admin_username(db))
    return ApiKeyCreated(**base.model_dump(), key=raw)


@router.put("/{key_id}/status", response_model=ApiKeyResponse)
def set_key_status(key_id: int, active: bool, db: Session = Depends(get_db),
                   _: str = Depends(require_admin)):
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다")
    row.is_active = active
    db.commit()
    db.refresh(row)
    user = db.query(User).filter(User.id == row.user_id).first() if row.user_id else None
    return _to_response(row, user, get_admin_username(db))


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(key_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="키를 찾을 수 없습니다")
    db.delete(row)
    db.commit()
