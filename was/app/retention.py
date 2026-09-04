"""로그 보존 기간(개월) — 화면·파티션 생성·백업 스크립트가 공유하는 단일 소스.

값은 app_config('retention_months')에 저장한다. 예전에는 같은 숫자가 세 곳
(routers/settings.py 상수, main.PARTITION_PAST_MONTHS, backup_db.sh)에 흩어져 있어
한 곳만 바꾸면 조용히 어긋났다.
"""
import logging

from sqlalchemy.orm import Session

from app.security import get_config, set_config

log = logging.getLogger("soltrace.retention")

RETENTION_KEY = "retention_months"
DEFAULT_RETENTION_MONTHS = 36
MIN_RETENTION_MONTHS = 1
MAX_RETENTION_MONTHS = 120


def get_retention_months(db: Session) -> int:
    """저장된 보존 기간. 값이 없거나 망가졌으면 기본값(36)."""
    raw = get_config(db, RETENTION_KEY)
    try:
        months = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_MONTHS
    if not MIN_RETENTION_MONTHS <= months <= MAX_RETENTION_MONTHS:
        log.warning("retention_months 값이 범위를 벗어남(%s) — 기본값 사용", months)
        return DEFAULT_RETENTION_MONTHS
    return months


def set_retention_months(db: Session, months: int) -> None:
    if not MIN_RETENTION_MONTHS <= months <= MAX_RETENTION_MONTHS:
        raise ValueError(
            f"보존 기간은 {MIN_RETENTION_MONTHS}~{MAX_RETENTION_MONTHS}개월 사이여야 합니다"
        )
    set_config(db, RETENTION_KEY, str(months))
