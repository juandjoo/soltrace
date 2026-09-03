import logging
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import alert_settings, notifier
from app.config import settings as cfg
from app.database import get_db
from app.deps import require_admin
from app.gitinfo import git, git_run
from app.schemas import (
    AlertSettings, AlertSettingsInfo,
    PasswordChangeRequest, UpdateTriggerResponse, VersionInfo, NotifySettings,
    StorageInfo, StoragePartition,
)
from app.security import (
    client_ip_from_request, parse_ip_entries,
    verify_admin_password, set_admin_password,
    get_admin_username, set_admin_username,
    get_office_ips, set_office_ips,
    get_config, set_config,
)

log = logging.getLogger("soltrace.settings")

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _version_info(check_remote: bool = False) -> VersionInfo:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    commit = git("rev-parse", "--short", "HEAD")
    commit_date = git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M")
    subject = git("log", "-1", "--format=%s")
    info = VersionInfo(branch=branch, commit=commit, commit_date=commit_date, subject=subject)

    if check_remote and branch:
        # 원격 fetch 는 네트워크 작업 → 넉넉한 타임아웃. 실패하면 '최신'으로 오인하지
        # 않도록 에러를 표면화한다 (소유권/인증/네트워크 문제 가시화).
        r = git_run("fetch", "--quiet", "origin", branch, timeout=30)
        if r is None or r.returncode != 0:
            info.error = ((r.stderr.strip() if r else "") or "git fetch 실패")[:300]
            return info
        behind = git("rev-list", "--count", f"HEAD..origin/{branch}")
        if behind is not None and behind.isdigit():
            info.behind = int(behind)
            info.update_available = int(behind) > 0
            info.checked = True
    return info


@router.get("/version", response_model=VersionInfo)
def get_version(_: str = Depends(require_admin)):
    return _version_info()


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeRequest,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    if not verify_admin_password(db, body.current_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 일치하지 않습니다")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="새 비밀번호가 기존과 동일합니다")
    set_admin_password(db, body.new_password)


@router.post("/check-update", response_model=VersionInfo)
def check_update(_: str = Depends(require_admin)):
    return _version_info(check_remote=True)


# ── DB 저장소 현황 ────────────────────────────────────────────────────────────
# backup_db.sh 의 RETENTION_MONTHS 와 동일. 파티션 자동 생성 범위(main.PARTITION_PAST_MONTHS)도 이 값.
RETENTION_MONTHS = 36


@router.get("/storage", response_model=StorageInfo)
def get_storage(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """ftp_logs 파티션별 크기/행수 + default 파티션 잔존 여부.

    행수는 pg_class.reltuples(통계 추정치)로 읽어 수억 행이어도 즉시 응답한다.
    default 파티션만 정확히 센다 — 재배치 필요 여부 판단용이며 보통 0이어야 한다.
    """
    rows = db.execute(text("""
        SELECT c.relname,
               GREATEST(c.reltuples, 0)::bigint          AS rows_est,
               pg_total_relation_size(c.oid)             AS total_bytes,
               pg_relation_size(c.oid)                   AS table_bytes,
               pg_indexes_size(c.oid)                    AS index_bytes
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'ftp_logs'
         ORDER BY c.relname DESC
    """)).fetchall()
    parts = [StoragePartition(name=r.relname, rows_est=r.rows_est, total_bytes=r.total_bytes,
                              table_bytes=r.table_bytes, index_bytes=r.index_bytes) for r in rows]
    db_bytes = db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
    default_rows = db.execute(text("SELECT COUNT(*) FROM ftp_logs_default")).scalar() or 0
    default_months = []
    if default_rows:
        default_months = [r[0] for r in db.execute(text(
            "SELECT DISTINCT to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM') "
            "FROM ftp_logs_default ORDER BY 1"
        )).fetchall()]
    return StorageInfo(
        db_bytes=db_bytes,
        ftp_logs_bytes=sum(p.total_bytes for p in parts),
        partitions=parts,
        default_rows=default_rows,
        default_months=default_months,
        retention_months=RETENTION_MONTHS,
    )


# ── 계정 보안 ─────────────────────────────────────────────────────────────────

@router.get("/security")
def get_security(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return {
        "username": get_admin_username(db),
        "allowed_ips": get_office_ips(db),
        "my_ip": client_ip_from_request(request),
    }


@router.put("/allowed-ips", status_code=status.HTTP_204_NO_CONTENT)
def update_allowed_ips(
    body: dict,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    ips = body.get("allowed_ips", [])
    if not isinstance(ips, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="allowed_ips must be a list")
    valid, invalid = parse_ip_entries(ips)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"유효하지 않은 IP/CIDR: {', '.join(invalid)}",
        )
    set_office_ips(db, valid)


@router.get("/notify", response_model=NotifySettings)
def get_notify(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    def _g(key, default=""):
        return get_config(db, key) or default
    return NotifySettings(
        webhook_url=_g("notify_webhook_url"),
        hms_url=_g("notify_hms_url"),
    )


@router.put("/notify", status_code=status.HTTP_204_NO_CONTENT)
def save_notify(body: NotifySettings, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    for url_field, label in ((body.webhook_url, "웹훅"), (body.hms_url, "HMS")):
        if url_field:
            try:
                notifier.validate_webhook_url(url_field)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{label} {e}")
    for k, v in {"notify_webhook_url": body.webhook_url, "notify_hms_url": body.hms_url}.items():
        set_config(db, k, v)


@router.get("/alerts", response_model=AlertSettingsInfo)
def get_alerts(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return AlertSettingsInfo(
        **alert_settings.load(db),
        large_file_bytes=cfg.alert_large_file_bytes,
        bucket_minutes=cfg.alert_bucket_minutes,
        baseline_days=cfg.alert_baseline_days,
    )


@router.put("/alerts", response_model=AlertSettingsInfo)
def save_alerts(body: AlertSettings, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    alert_settings.save(db, body.model_dump(exclude_unset=True))
    log.info("Alert thresholds updated: %s", body.model_dump(exclude_none=True))
    return get_alerts(db)


@router.post("/alerts/reset", response_model=AlertSettingsInfo)
def reset_alerts(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """.env 기본값으로 되돌린다."""
    alert_settings.reset(db)
    return get_alerts(db)


@router.post("/notify/test", status_code=status.HTTP_204_NO_CONTENT)
def test_notify(
    channel: str = "all",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """channel: 'all' | 'webhook' | 'hms'"""
    if channel not in ("all", "webhook", "hms"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"알 수 없는 채널: {channel!r}")
    dummy = [{
        "device_id": None,
        "device_hostname": "SolTrace-Test",
        "bucket": datetime.now(timezone.utc),
        "metric": "fail_rate",
        "severity": "warning",
        "value": 0.15,
        "baseline": 0.02,
        "test": True,
    }]
    if not notifier.channels_configured(db):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="알림 채널이 설정되지 않았습니다")
    ok = notifier.dispatch(dummy, db, channel=channel)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="발송 실패 — 로그를 확인하세요")


@router.get("/notify/mute", response_model=dict)
def get_mute(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return {"muted": notifier.is_muted(db)}


@router.post("/notify/mute", status_code=status.HTTP_204_NO_CONTENT)
def set_mute(muted: bool, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    set_config(db, "notify_muted", "true" if muted else "false")
    log.info("Notifications %s", "muted" if muted else "unmuted")


def _run_selfupdate():
    """응답 전송 후 백그라운드에서 실행 — WAS 재시작과 HTTP 응답 경쟁 방지."""
    try:
        out = subprocess.run(
            ["sudo", "-n", cfg.selfupdate_cmd],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            log.error("selfupdate failed: %s", (out.stderr or out.stdout).strip())
    except (OSError, subprocess.SubprocessError) as e:
        log.error("selfupdate error: %s", e)


@router.post("/update", response_model=UpdateTriggerResponse)
def trigger_update(background_tasks: BackgroundTasks, _: str = Depends(require_admin)):
    background_tasks.add_task(_run_selfupdate)
    return UpdateTriggerResponse(
        started=True,
        message="업데이트를 시작했습니다. 잠시 후 서비스가 재시작되며 완료까지 1~2분 걸립니다.",
    )
