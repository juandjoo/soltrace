import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, create_access_token, get_current_user
from app.schemas import LoginRequest, TokenResponse
from app.security import (
    LOGIN_LOCK_MINUTES, check_ip_allowed, check_user_ip_allowed, clear_failed_login,
    client_ip_from_request, get_admin_username, get_user, lock_seconds_left,
    register_failed_login, verify_admin_credentials, verify_password,
    _DUMMY_HASH,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _ip_blocked(client_ip: str):
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "IP_BLOCKED", "client_ip": client_ip},
    )


def _bad_credentials(remaining: int | None = None):
    """자격증명 실패. 남은 시도 횟수를 알려줘 잠기기 전에 알아차리게 한다."""
    detail = "Incorrect credentials"
    if remaining is not None and remaining > 0:
        detail = f"아이디 또는 비밀번호가 올바르지 않습니다. (남은 시도 {remaining}회)"
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """관리자·고객 계정 모두 users 테이블로 검증한다.

    users 에 아직 관리자 행이 없는 최초 기동(이관 전)에는 app_config 부트스트랩으로
    떨어진다 — 이관이 실패해도 관리자가 잠기지 않게 남겨 둔 경로다.
    비밀번호 연속 실패는 계정에 기록되어 LOGIN_MAX_ATTEMPTS 회에서 잠긴다.
    """
    client_ip = client_ip_from_request(request)
    user = get_user(db, req.username)

    # ── 레거시: users 로 이관되기 전의 부트스트랩 관리자 ─────────────────────
    if user is None:
        if hmac.compare_digest(req.username, get_admin_username(db)):
            if not check_ip_allowed(db, client_ip):
                raise _ip_blocked(client_ip)
            if not verify_admin_credentials(db, req.username, req.password):
                raise _bad_credentials()
            return TokenResponse(access_token=create_access_token(req.username, "admin"), role="admin")
        # 계정이 없어도 검증을 수행해 타이밍으로 존재 여부가 새지 않게 한다
        verify_password(req.password, _DUMMY_HASH)
        raise _bad_credentials()

    # ── 잠금 / 비활성 ────────────────────────────────────────────────────────
    left = lock_seconds_left(user)
    if left:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCOUNT_LOCKED", "seconds": left,
                    "message": f"비밀번호를 여러 번 틀려 잠겼습니다. {left // 60 + 1}분 후 다시 시도하세요."},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"code": "ACCOUNT_DISABLED", "message": "비활성화된 계정입니다."})

    # ── 접속 IP ──────────────────────────────────────────────────────────────
    # 관리자는 전역 허용 IP(설정 > 계정 보안)를 먼저 통과해야 한다.
    if user.role == "admin" and not check_ip_allowed(db, client_ip):
        raise _ip_blocked(client_ip)
    if not check_user_ip_allowed(user.allowed_ips, client_ip):
        raise _ip_blocked(client_ip)

    # ── 비밀번호 ─────────────────────────────────────────────────────────────
    if not verify_password(req.password, user.password_hash):
        remaining = register_failed_login(db, user)
        if remaining == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ACCOUNT_LOCKED", "seconds": LOGIN_LOCK_MINUTES * 60,
                        "message": f"비밀번호를 {LOGIN_LOCK_MINUTES}분간 잠갔습니다. 관리자에게 해제를 요청하세요."},
            )
        raise _bad_credentials(remaining)

    clear_failed_login(db, user)
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
