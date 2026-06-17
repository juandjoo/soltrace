from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import create_access_token, require_admin
from app.schemas import LoginRequest, TokenResponse
from app.security import check_ip_allowed, verify_admin_credentials

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "0.0.0.0"
    if not check_ip_allowed(db, client_ip):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "IP_BLOCKED", "client_ip": client_ip},
        )
    if not verify_admin_credentials(db, req.username, req.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect credentials")
    return TokenResponse(access_token=create_access_token())


@router.post("/refresh", response_model=TokenResponse)
def refresh(_: str = Depends(require_admin)):
    return TokenResponse(access_token=create_access_token())
