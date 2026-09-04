"""디스크가 임계치를 넘으면 오래된 월 파티션부터 지운다.

사용자 결정(2026-09-04): **백업 파일 존재 여부와 무관하게** 오래된 월부터 삭제한다.
복구 불가능한 삭제이므로 실행 전후로 로그와 알림을 남긴다.

안전장치
  - 당월/미래 파티션은 대상에서 제외한다 (수집 중인 데이터).
  - 데이터가 있는 파티션이 하나만 남으면 멈춘다 (전부 비우지 않는다).
  - 한 주기에 한 개만 지운다 — 지운 뒤 사용률을 다시 재고, 여전히 높으면 다음 주기에 계속.
"""
import logging
import shutil
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import notifier
from app.security import get_config, set_config

log = logging.getLogger("soltrace.disk_guard")

DISK_PATH = "/"
ENABLED_KEY = "disk_autopurge_enabled"
THRESHOLD_KEY = "disk_autopurge_percent"
DEFAULT_THRESHOLD = 90
MIN_THRESHOLD = 50
MAX_THRESHOLD = 99


def is_enabled(db: Session) -> bool:
    return (get_config(db, ENABLED_KEY) or "true").lower() != "false"


def get_threshold(db: Session) -> int:
    raw = get_config(db, THRESHOLD_KEY)
    try:
        pct = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return pct if MIN_THRESHOLD <= pct <= MAX_THRESHOLD else DEFAULT_THRESHOLD


def save_settings(db: Session, enabled: bool, threshold: int) -> None:
    if not MIN_THRESHOLD <= threshold <= MAX_THRESHOLD:
        raise ValueError(f"임계치는 {MIN_THRESHOLD}~{MAX_THRESHOLD}% 사이여야 합니다")
    set_config(db, ENABLED_KEY, "true" if enabled else "false")
    set_config(db, THRESHOLD_KEY, str(threshold))


def usage(path: str = DISK_PATH) -> tuple[int, int, float]:
    """(총량, 사용량, 사용률%). 조회 실패 시 (0, 0, 0.0).

    자동 정리 판정과 설정 화면 표시가 같은 값을 봐야 하므로 한 번의 조회로 셋을 다 낸다
    (예전에는 화면이 total/used 를, 판정이 percent 를 따로 재 syscall 이 두 번 났다).
    """
    try:
        du = shutil.disk_usage(path)
    except OSError as e:
        log.warning("디스크 사용량 조회 실패(%s): %s", path, e)
        return 0, 0, 0.0
    if not du.total:
        return 0, 0, 0.0
    used = du.total - du.free
    return du.total, used, round(used / du.total * 100, 1)


def disk_percent(path: str = DISK_PATH) -> float:
    return usage(path)[2]


def _purgeable_partitions(db: Session) -> list[str]:
    """데이터가 있는 월 파티션을 오래된 순으로. 당월 이후는 제외한다."""
    now = datetime.now(timezone.utc)
    current = f"ftp_logs_{now.year:04d}_{now.month:02d}"
    rows = db.execute(text("""
        SELECT c.relname
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'ftp_logs'
           AND c.relname ~ '^ftp_logs_[0-9]{4}_[0-9]{2}$'
         ORDER BY c.relname
    """)).scalars().all()
    older = [n for n in rows if n < current]
    # 실제 데이터가 있는 것만 (미리 만들어 둔 빈 파티션을 지워봐야 공간이 안 는다)
    return [n for n in older
            if db.execute(text(f"SELECT EXISTS(SELECT 1 FROM {n})")).scalar()]


def enforce(db: Session) -> str | None:
    """임계치를 넘었으면 가장 오래된 월 파티션 하나를 삭제한다. 삭제한 이름 또는 None."""
    if not is_enabled(db):
        return None
    threshold = get_threshold(db)
    pct = disk_percent()
    if pct < threshold:
        return None

    targets = _purgeable_partitions(db)
    if len(targets) <= 1:
        log.error("디스크 %.1f%% (임계 %d%%) — 지울 수 있는 과거 파티션이 없습니다. "
                  "수동 조치가 필요합니다.", pct, threshold)
        _notify(db, f"디스크 {pct}% — 지울 과거 파티션이 없어 자동 정리를 멈췄습니다. 수동 조치가 필요합니다.")
        return None

    victim = targets[0]
    log.warning("디스크 %.1f%% (임계 %d%%) — 가장 오래된 파티션 %s 를 삭제합니다 (백업 여부 무관, 설정에 따름)",
                pct, threshold, victim)
    db.execute(text(f"DROP TABLE IF EXISTS public.{victim}"))
    db.commit()
    after = disk_percent()
    log.warning("파티션 %s 삭제 완료 — 디스크 %.1f%% → %.1f%%", victim, pct, after)
    _notify(db, f"디스크 {pct}% 가 임계({threshold}%)를 넘어 가장 오래된 로그 파티션 "
                f"{victim} 을 삭제했습니다. (현재 {after}%)")
    return victim


def _notify(db: Session, message: str) -> None:
    """알림 채널이 설정돼 있으면 알린다. 실패해도 정리 자체는 계속된다."""
    try:
        if notifier.channels_configured(db):
            notifier.send_text(db, "[SolTrace] 디스크 자동 정리", message)
    except Exception as e:                       # 알림 실패로 정리를 막지 않는다
        log.error("디스크 정리 알림 실패: %s", e)
