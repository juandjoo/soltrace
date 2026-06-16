import subprocess

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.database import get_db
from app.deps import require_admin
from app.schemas import PasswordChangeRequest, UpdateTriggerResponse, VersionInfo
from app.security import verify_admin_password, set_admin_password

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _git(*args: str, timeout: int = 10) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cfg.repo_dir, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _version_info(check_remote: bool = False) -> VersionInfo:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    commit = _git("rev-parse", "--short", "HEAD")
    commit_date = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M")
    subject = _git("log", "-1", "--format=%s")
    info = VersionInfo(branch=branch, commit=commit, commit_date=commit_date, subject=subject)

    if check_remote and branch:
        # 원격 fetch 는 네트워크 작업 → 넉넉한 타임아웃
        _git("fetch", "--quiet", "origin", branch, timeout=30)
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


@router.post("/update", response_model=UpdateTriggerResponse)
def trigger_update(_: str = Depends(require_admin)):
    # soltrace 가 sudo NOPASSWD 로 root 소유 래퍼만 실행. 래퍼는 systemd-run 으로
    # 분리 유닛에서 배포를 돌려 WAS 재시작에도 끝까지 진행된다 (여기선 즉시 반환).
    try:
        out = subprocess.run(
            ["sudo", "-n", cfg.selfupdate_cmd],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"업데이트 실행 실패: {e}")
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "알 수 없는 오류").strip()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"업데이트 시작 실패: {detail}")
    return UpdateTriggerResponse(
        started=True,
        message="업데이트를 시작했습니다. 잠시 후 서비스가 재시작되며 완료까지 1~2분 걸립니다.",
    )
