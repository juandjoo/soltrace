import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, create_access_token, get_current_user
from app.schemas import LoginRequest, TokenResponse
from app.security import (
    check_ip_allowed, check_user_ip_allowed, client_ip_from_request,
    get_active_user, get_admin_username, verify_admin_credentials, verify_password,
    _DUMMY_HASH,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _ip_blocked(client_ip: str):
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "IP_BLOCKED", "client_ip": client_ip},
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = client_ip_from_request(request)

    # ── admin 계정 (app_config 부트스트랩, 전역 office IP 화이트리스트) ──────────
    if hmac.compare_digest(req.username, get_admin_username(db)):
        if not check_ip_allowed(db, client_ip):
            raise _ip_blocked(client_ip)
        if not verify_admin_credentials(db, req.username, req.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials")
        return TokenResponse(access_token=create_access_token(req.username, "admin"), role="admin")

    # ── 고객 계정 (users 테이블, 계정별 IP 화이트리스트) ────────────────────────
    user = get_active_user(db, req.username)
    # 사용자 부재 시에도 더미 해시로 검증해 타이밍 사이드채널 방지
    pw_ok = verify_password(req.password, user.password_hash if user else _DUMMY_HASH)
    if not (user and pw_ok):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials")
    if not check_user_ip_allowed(user.allowed_ips, client_ip):
        raise _ip_blocked(client_ip)
    return TokenResponse(
        access_token=create_access_token(user.username, user.role, user.customer),
        role=user.role, customer=user.customer,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(user: Principal = Depends(get_current_user)):
    return TokenResponse(
        access_token=create_access_token(user.username, user.role, user.customer),
        role=user.role, customer=user.customer,
    )
