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
from app.security import get_admin_username, parse_ip_entries

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


def validate_ip_entries(entries: list) -> list[str]:
    """IP/CIDR 목록을 검증해 유효 항목만 돌려준다. 하나라도 틀리면 422.

    전역 허용 IP(설정 > 계정 보안)와 계정별 허용 IP(고객 계정 관리)가 같은 규칙·같은
    오류 문구를 쓰도록 한 곳에 둔다 (검증 규칙 자체는 security.parse_ip_entries).
    """
    valid, invalid = parse_ip_entries(entries)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"유효하지 않은 IP/CIDR: {', '.join(invalid)}",
        )
    return valid


def require_admin(user: Principal = Depends(get_current_user)) -> Principal:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def device_scope(
    user: Principal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Optional[list[int]]:
    """조회 격리용 허용 device_id 목록.

    - admin    → None (필터 없음, 전체 접근)
    - customer → **FTP 계정 매핑에 등록된 그룹**의 device_id 목록

    매핑(user_ftp_accounts)이 하나도 없으면 빈 리스트 → 아무것도 보이지 않는다.
    그룹만으로 열어두지 않는 것은 운영 결정이다(2026-09-06) — 한 그룹의 장비를 여러
    고객사가 함께 쓰는 경우가 있어, "이 계정이 볼 FTP 아이디"를 명시해야 열린다.
    그룹이 그 고객사의 것인지(g.customer)도 함께 확인해 매핑이 잘못 들어가도
    다른 고객사 장비가 새지 않게 한다.
    """
    if user.is_admin:
        return None
    rows = db.execute(
        text(
            "SELECT DISTINCT dg.device_id "
            "FROM user_ftp_accounts ufa "
            "JOIN users u ON u.id = ufa.user_id "
            "JOIN groups g ON g.id = ufa.group_id AND g.customer = u.customer "
            "JOIN device_groups dg ON dg.group_id = ufa.group_id "
            "WHERE u.username = :u AND u.customer = :c"
        ),
        {"u": user.username, "c": user.customer or ""},
    ).scalars().all()
    return list(rows)


def ftp_scope(
    user: Principal = Depends(get_current_user),
) -> Optional[str]:
    """로그 조회를 제한할 계정명 — admin 은 None(제한 없음), 고객 계정은 본인 아이디.

    device_scope 가 '어느 장비까지'라면 이쪽은 '그 장비의 어느 FTP 아이디까지'다.
    조건 SQL 은 ftp_scope_sql() 한 곳에만 둔다(로그 조회·내보내기·대시보드가 같은 규칙).
    """
    return None if user.is_admin else (user.username or "")


def ftp_scope_sql(params: dict, scope_user: Optional[str],
                  dev_col: str, user_col: str) -> str:
    """ftp_logs 조회에 붙일 FTP 계정 매핑 조건 (admin 이면 빈 문자열).

    "이 로그의 (장비, FTP 아이디) 조합이 그 계정에 등록돼 있는가"를 EXISTS 로 묻는다.
    매핑 표를 직접 보므로 관리자가 매핑을 고치면 다음 조회부터 바로 반영되고,
    허용 목록을 파라미터로 실어 나르지 않아 조합이 많아도 쿼리가 커지지 않는다.
    """
    if scope_user is None:
        return ""
    params["ftp_scope_user"] = scope_user
    return (f" AND EXISTS (SELECT 1 FROM user_ftp_accounts ufa"
            f" JOIN users u2 ON u2.id = ufa.user_id"
            f" JOIN groups g2 ON g2.id = ufa.group_id AND g2.customer = u2.customer"
            f" JOIN device_groups dg2 ON dg2.group_id = ufa.group_id"
            f" WHERE u2.username = :ftp_scope_user"
            f" AND dg2.device_id = {dev_col} AND ufa.ftp_username = {user_col})")
