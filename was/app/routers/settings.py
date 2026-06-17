import logging
import subprocess

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

log = logging.getLogger("soltrace.settings")

from app.config import settings as cfg
from app.database import get_db
from app.deps import require_admin
from app.schemas import PasswordChangeRequest, UpdateTriggerResponse, VersionInfo, NotifySettings
import ipaddress

from app.security import (
    verify_admin_password, set_admin_password,
    get_admin_username, set_admin_username,
    get_office_ips, set_office_ips,
    get_config, set_config,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _git_run(*args: str, timeout: int = 10):
    # safe.directory: WAS(soltrace)가 root 소유로 바뀐 .git 에서도 git 실행 가능하게
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={cfg.repo_dir}", "-C", cfg.repo_dir, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git(*args: str, timeout: int = 10) -> str | None:
    r = _git_run(*args, timeout=timeout)
    return r.stdout.strip() if (r and r.returncode == 0) else None


def _version_info(check_remote: bool = False) -> VersionInfo:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    commit = _git("rev-parse", "--short", "HEAD")
    commit_date = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M")
    subject = _git("log", "-1", "--format=%s")
    info = VersionInfo(branch=branch, commit=commit, commit_date=commit_date, subject=subject)

    if check_remote and branch:
        # 원격 fetch 는 네트워크 작업 → 넉넉한 타임아웃. 실패하면 '최신'으로 오인하지
        # 않도록 에러를 표면화한다 (소유권/인증/네트워크 문제 가시화).
        r = _git_run("fetch", "--quiet", "origin", branch, timeout=30)
        if r is None or r.returncode != 0:
            info.error = ((r.stderr.strip() if r else "") or "git fetch 실패")[:300]
            return info
        behind = _git("rev-list", "--count", f"HEAD..origin/{branch}")
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


# ── 계정 보안 ─────────────────────────────────────────────────────────────────

@router.get("/security")
def get_security(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    # 리버스 프록시(nginx) 뒤에서는 client.host가 127.0.0.1이므로 헤더 우선 참조
    xff = request.headers.get("x-forwarded-for")
    my_ip = xff.split(",")[0].strip() if xff else (
        request.headers.get("x-real-ip") or (request.client.host if request.client else None)
    )
    return {
        "username": get_admin_username(db),
        "allowed_ips": get_office_ips(db),
        "my_ip": my_ip,
    }



def _validate_ip_list(entries: list) -> list[str]:
    invalid = []
    for entry in entries:
        if not isinstance(entry, str):
            invalid.append(str(entry))
            continue
        try:
            ipaddress.ip_network(entry.strip(), strict=False)
        except ValueError:
            invalid.append(entry)
    return invalid


@router.put("/allowed-ips", status_code=status.HTTP_204_NO_CONTENT)
def update_allowed_ips(
    body: dict,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    ips = body.get("allowed_ips", [])
    if not isinstance(ips, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="allowed_ips must be a list")
    invalid = _validate_ip_list(ips)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"유효하지 않은 IP/CIDR: {', '.join(invalid)}",
        )
    set_office_ips(db, ips)


_PW_MASK = "***"


@router.get("/notify", response_model=NotifySettings)
def get_notify(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    def _g(key, default=""):
        return get_config(db, key) or default
    pw_stored = bool(get_config(db, "notify_smtp_password"))
    return NotifySettings(
        webhook_url=_g("notify_webhook_url"),
        smtp_host=_g("notify_smtp_host"),
        smtp_port=int(_g("notify_smtp_port", "587")),
        smtp_tls=_g("notify_smtp_tls", "true").lower() != "false",
        smtp_user=_g("notify_smtp_user"),
        smtp_password=_PW_MASK if pw_stored else "",   # 평문 미노출
        smtp_from=_g("notify_smtp_from"),
        email_to=_g("notify_email_to"),
        hms_url=_g("notify_hms_url"),
    )


@router.put("/notify", status_code=status.HTTP_204_NO_CONTENT)
def save_notify(body: NotifySettings, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from app.notifier import validate_webhook_url
    for url_field, label in ((body.webhook_url, "웹훅"), (body.hms_url, "HMS")):
        if url_field:
            try:
                validate_webhook_url(url_field)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{label} {e}")

    pairs = {
        "notify_webhook_url":  body.webhook_url,
        "notify_smtp_host":    body.smtp_host,
        "notify_smtp_port":    str(body.smtp_port),
        "notify_smtp_tls":     "true" if body.smtp_tls else "false",
        "notify_smtp_user":    body.smtp_user,
        "notify_smtp_from":    body.smtp_from,
        "notify_email_to":     body.email_to,
        "notify_hms_url":      body.hms_url,
    }
    for k, v in pairs.items():
        set_config(db, k, v)
    # 마스크값이면 기존 비밀번호 유지, 변경된 경우만 저장
    if body.smtp_password and body.smtp_password != _PW_MASK:
        set_config(db, "notify_smtp_password", body.smtp_password)


@router.post("/notify/test", status_code=status.HTTP_204_NO_CONTENT)
def test_notify(
    channel: str = "all",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """channel: 'all' | 'webhook' | 'hms' | 'email'"""
    from datetime import datetime, timezone
    from app import notifier
    if channel not in ("all", "webhook", "hms", "email"):
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
    from app import notifier
    return {"muted": notifier.is_muted(db)}


@router.post("/notify/mute", status_code=status.HTTP_204_NO_CONTENT)
def set_mute(muted: bool, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from app.security import set_config
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
