"""서비스 영향도 판정 임계값 — .env 기본값을 app_config(설정 페이지)에서 덮어쓴다.

service_monitor 가 판정 주기마다 load(db) 로 읽으므로 저장하면 다음 주기에 바로 반영된다
(WAS 재시작 불필요).

여기 포함하지 않는 값:
  - alert_large_file_bytes : 롤업 시점에 집계(service_metrics.*_big)에 반영되는 값이라
    바꾸면 과거 버킷과 기준이 어긋난다. .env 전용으로 두고 변경 시 배포로만 적용한다.
  - alert_bucket_minutes / alert_rollup_interval_sec : 스레드 기동 시 1회 읽는 값.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.security import get_config, set_config

_PREFIX = "alert_"

# 필드명: (형변환, config.Settings 의 기본값 속성)
FIELDS: dict[str, tuple] = {
    "mad_k":                 (float, "alert_mad_k"),
    "fail_rate_floor":       (float, "alert_fail_rate_floor"),
    "login_fail_rate_floor": (float, "alert_login_fail_rate_floor"),
    "throughput_drop":       (float, "alert_throughput_drop"),
    "cwd_fail_floor":        (int,   "alert_cwd_fail_floor"),
    "min_samples":           (int,   "alert_min_samples"),
    "min_login_samples":     (int,   "alert_min_login_samples"),
    "min_cwd_samples":       (int,   "alert_min_cwd_samples"),
    "min_large_samples":     (int,   "alert_min_large_samples"),
}


def defaults() -> dict:
    """.env(또는 코드) 기본값."""
    return {name: cast(getattr(settings, attr)) for name, (cast, attr) in FIELDS.items()}


def load(db: Session) -> dict:
    """현재 적용 임계값. DB 값이 없거나 깨졌으면 기본값으로 대체한다."""
    out = {}
    for name, (cast, attr) in FIELDS.items():
        raw = get_config(db, _PREFIX + name)
        try:
            out[name] = cast(raw) if raw not in (None, "") else cast(getattr(settings, attr))
        except (TypeError, ValueError):
            out[name] = cast(getattr(settings, attr))
    return out


def save(db: Session, values: dict) -> None:
    """전달된 필드만 저장 (None 은 건너뜀)."""
    for name, (cast, _) in FIELDS.items():
        v = values.get(name)
        if v is not None:
            set_config(db, _PREFIX + name, str(cast(v)))


def reset(db: Session, name: Optional[str] = None) -> None:
    """DB 값을 지워 기본값으로 되돌린다. name 미지정이면 전체."""
    targets = [name] if name else list(FIELDS)
    for n in targets:
        if n in FIELDS:
            set_config(db, _PREFIX + n, "")
