import logging
import subprocess

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

log = logging.getLogger("soltrace.settings")

from app.config import settings as cfg
from app.database import get_db
from app.deps import require_admin
from app.schemas import PasswordChangeRequest, UpdateTriggerResponse, VersionInfo, NotifySettings
from app.security import verify_admin_password, set_admin_password, get_config, set_config

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
        hms_telco=_g("notify_hms_telco"),
        hms_svc=_g("notify_hms_svc"),
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
        "notify_hms_telco":    body.hms_telco,
        "notify_hms_svc":      body.hms_svc,
    }
    for k, v in pairs.items():
        set_config(db, k, v)
    # 마스크값이면 기존 비밀번호 유지, 변경된 경우만 저장
    if body.smtp_password and body.smtp_password != _PW_MASK:
        set_config(db, "notify_smtp_password", body.smtp_password)


@router.post("/notify/test", status_code=status.HTTP_204_NO_CONTENT)
def test_notify(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    from datetime import datetime, timezone
    from app import notifier
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
    ok = notifier.dispatch(dummy, db)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="발송 실패 — 로그를 확인하세요")


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
