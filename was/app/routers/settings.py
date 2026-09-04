import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import alert_settings, disk_guard, notifier, retention
from app.config import settings as cfg
from app.database import get_db
from app.deps import Principal, require_admin
from app.gitinfo import git, git_run, repo_dir
from app.schemas import (
    AlertSettings, AlertSettingsInfo,
    PasswordChangeRequest, UpdateTriggerResponse, VersionInfo, NotifySettings,
    StorageInfo, StoragePartition, RetentionUpdate, DiskPurgeUpdate,
    ChangelogEntry, ChangelogItem,
)
from app.security import (
    client_ip_from_request, get_user, hash_password, parse_ip_entries, verify_password,
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


# changelog.md 한 줄: "- 제목 (`abc1234`)" — 커밋 해시는 있을 수도 없을 수도 있다.
_CHANGELOG_ITEM = re.compile(r"^-\s+(?P<text>.*?)(?:\s*\(`(?P<commit>[0-9a-f]{7,40})`\))?\s*$")
_CHANGELOG_DATE = re.compile(r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")


@router.get("/changelog", response_model=list[ChangelogEntry])
def get_changelog(
    limit: int = Query(default=30, ge=1, le=200),
    _: str = Depends(require_admin),
):
    """배포된 저장소의 changelog.md 를 날짜별로 파싱해 돌려준다.

    버전 정보(git)와 같은 저장소 경로를 본다 — 화면에 보이는 이력이 실제로 배포된
    코드의 이력이 되도록.
    """
    path = os.path.join(repo_dir(), "changelog.md")
    try:
        with open(path, encoding="utf-8") as fp:
            lines = fp.read().splitlines()
    except OSError as e:
        log.warning("changelog 읽기 실패: %s", e)
        return []

    entries: list[ChangelogEntry] = []
    for line in lines:
        d = _CHANGELOG_DATE.match(line)
        if d:
            if len(entries) >= limit:
                break
            entries.append(ChangelogEntry(date=d.group("date")))
            continue
        if not entries or not line.startswith("- "):
            continue
        m = _CHANGELOG_ITEM.match(line)
        if m and m.group("text"):
            entries[-1].items.append(ChangelogItem(text=m.group("text"), commit=m.group("commit")))
    return entries


@router.get("/version", response_model=VersionInfo)
def get_version(_: str = Depends(require_admin)):
    return _version_info()


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeRequest,
    db: Session = Depends(get_db),
    me: Principal = Depends(require_admin),
):
    """로그인한 관리자 본인의 비밀번호를 바꾼다.

    다른 관리자의 비밀번호는 설정 > 관리자 계정에서 재설정한다.
    users 에 행이 없는 부트스트랩 관리자는 예전처럼 app_config 를 고친다.
    """
    if body.new_password == body.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="새 비밀번호가 기존과 동일합니다")
    user = get_user(db, me.username)
    if user is None:
        if not verify_admin_password(db, body.current_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 일치하지 않습니다")
        set_admin_password(db, body.new_password)
        return
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 일치하지 않습니다")
    user.password_hash = hash_password(body.new_password)
    db.commit()


@router.post("/check-update", response_model=VersionInfo)
def check_update(_: str = Depends(require_admin)):
    return _version_info(check_remote=True)


# ── DB 저장소 현황 ────────────────────────────────────────────────────────────
# 보존 기간은 app_config 한 곳에서만 읽는다 (app/retention.py).

# 월별 파티션 이름 — 삭제 API 가 임의 테이블을 지우지 못하게 형식부터 고정한다.
_PARTITION_NAME = re.compile(r"^ftp_logs_\d{4}_\d{2}$")

# 디스크 사용률 기준 경로 — 자동 정리와 같은 경로를 본다.
DISK_PATH = disk_guard.DISK_PATH


def _partition_ranges(db: Session, names: list[str]) -> dict[str, tuple]:
    """파티션별 실제 데이터 기간(min/max log_time).

    파티션은 보존 기간만큼 '미리' 만들어 두기 때문에 목록에 있다고 데이터가 있는 게
    아니다("2025년 데이터가 있다"로 보이던 원인). 여기서 실제 기간을 확인해 빈 파티션과
    구분한다. MIN/MAX 는 log_time 인덱스를 타므로 파티션이 커도 즉시 끝난다.
    이름은 pg_class 에서 온 값이지만 문자열로 넣으므로 형식을 한 번 더 확인한다.
    """
    safe = [n for n in names if _PARTITION_NAME.match(n) or n == "ftp_logs_default"]
    if not safe:
        return {}
    union = " UNION ALL ".join(
        f"SELECT '{n}' AS part, MIN(log_time) AS first_log, MAX(log_time) AS last_log FROM {n}"
        for n in safe
    )
    return {r.part: (r.first_log, r.last_log) for r in db.execute(text(union)).fetchall()}


def _disk_usage(path: str = DISK_PATH) -> tuple[int, int, float]:
    """총량·사용량·사용률. 사용률은 자동 정리(disk_guard)와 같은 계산을 쓴다."""
    try:
        du = shutil.disk_usage(path)
    except OSError as e:
        log.warning("디스크 사용량 조회 실패(%s): %s", path, e)
        return 0, 0, 0.0
    return du.total, du.total - du.free, disk_guard.disk_percent(path)


@router.get("/storage", response_model=StorageInfo)
def get_storage(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """ftp_logs 파티션별 크기/행수/실제 데이터 기간 + default 파티션 잔존 여부 + 디스크 사용률.

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
    ranges = _partition_ranges(db, [r.relname for r in rows])
    parts = []
    for r in rows:
        first_log, last_log = ranges.get(r.relname, (None, None))
        parts.append(StoragePartition(
            name=r.relname, rows_est=r.rows_est, total_bytes=r.total_bytes,
            table_bytes=r.table_bytes, index_bytes=r.index_bytes,
            has_rows=first_log is not None, first_log=first_log, last_log=last_log,
        ))
    db_bytes = db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
    default_rows = db.execute(text("SELECT COUNT(*) FROM ftp_logs_default")).scalar() or 0
    default_months = []
    if default_rows:
        default_months = [r[0] for r in db.execute(text(
            "SELECT DISTINCT to_char(log_time AT TIME ZONE 'UTC', 'YYYY-MM') "
            "FROM ftp_logs_default ORDER BY 1"
        )).fetchall()]
    disk_total, disk_used, disk_pct = _disk_usage()
    return StorageInfo(
        db_bytes=db_bytes,
        ftp_logs_bytes=sum(p.total_bytes for p in parts),
        partitions=parts,
        default_rows=default_rows,
        default_months=default_months,
        retention_months=retention.get_retention_months(db),
        disk_total_bytes=disk_total,
        disk_used_bytes=disk_used,
        disk_percent=disk_pct,
        autopurge_enabled=disk_guard.is_enabled(db),
        autopurge_percent=disk_guard.get_threshold(db),
    )


@router.put("/storage/autopurge", response_model=StorageInfo)
def update_autopurge(
    body: DiskPurgeUpdate,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    """디스크 임계치 초과 시 자동 삭제 설정.

    켜 두면 롤업 주기마다 확인해 **백업 여부와 무관하게** 가장 오래된 월 파티션부터
    지운다(운영 결정). 삭제 전후로 로그와 알림이 남는다.
    """
    try:
        disk_guard.save_settings(db, body.enabled, body.percent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    log.warning("디스크 자동 정리 설정 변경: enabled=%s threshold=%d%%", body.enabled, body.percent)
    return get_storage(db)


@router.put("/storage/retention", response_model=StorageInfo)
def update_retention(
    body: RetentionUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """보존 기간(개월) 변경. 파티션 자동 생성 범위와 백업 스크립트가 같은 값을 읽는다.

    줄이는 것만으로 기존 데이터가 지워지지는 않는다 — 실제 삭제는 야간 백업
    스크립트(백업이 끝난 파티션부터)나 아래 월별 삭제가 한다.
    """
    try:
        retention.set_retention_months(db, body.months)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    log.info("보존 기간 변경: %s개월", body.months)
    return get_storage(db)


@router.delete("/storage/partitions/{name}", response_model=StorageInfo)
def drop_partition(
    name: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """월별 파티션 1개를 DROP 한다 — 되돌릴 수 없다.

    안전장치: 이름 형식(ftp_logs_YYYY_MM) 확인 → 실제로 ftp_logs 의 파티션인지 확인 →
    당월/미래 파티션 거부(수집 중인 데이터를 지우지 않게).
    """
    if not _PARTITION_NAME.match(name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="월별 파티션(ftp_logs_YYYY_MM)만 삭제할 수 있습니다")
    is_partition = db.execute(text("""
        SELECT COUNT(*) FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'ftp_logs' AND c.relname = :n
    """), {"n": name}).scalar()
    if not is_partition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="파티션을 찾을 수 없습니다")

    now = datetime.now(timezone.utc)
    if name >= f"ftp_logs_{now.year:04d}_{now.month:02d}":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="당월 이후 파티션은 삭제할 수 없습니다")

    db.execute(text(f"DROP TABLE IF EXISTS public.{name}"))
    db.commit()
    log.warning("파티션 삭제: %s (관리자 요청)", name)
    return get_storage(db)


# ── 계정 보안 ─────────────────────────────────────────────────────────────────

@router.get("/security")
def get_security(request: Request, db: Session = Depends(get_db),
                 me: Principal = Depends(require_admin)):
    return {
        "username": me.username,
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
