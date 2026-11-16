"""업로드 자동 purge 이력 조회·재시도 (관리자 전용)."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Group, PurgeJob
from app import purge
from app.schemas import PurgeJobItem, PurgeRetryResult

router = APIRouter(prefix="/api/v1/purge", tags=["purge"])



@router.get("/jobs", response_model=List[PurgeJobItem])
def list_jobs(
    status: Optional[str] = Query(default=None, pattern="^(pending|done|failed)$"),
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """최근 순. q 는 파일 경로·FTP 계정 부분 일치."""
    query = db.query(PurgeJob, Group.name, Group.telco).outerjoin(Group, Group.id == PurgeJob.group_id)
    if status:
        query = query.filter(PurgeJob.status == status)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(PurgeJob.file_path.ilike(like, escape="\\") |
                             PurgeJob.ftp_username.ilike(like, escape="\\"))
    rows = query.order_by(PurgeJob.id.desc()).limit(limit).all()
    return [
        PurgeJobItem(
            id=j.id, group_id=j.group_id, group_name=gname, telco=telco,
            ftp_username=j.ftp_username, file_path=j.file_path, purge_path=j.purge_path,
            service=j.service, volume=j.volume, status=j.status, attempts=j.attempts,
            last_error=j.last_error, created_at=j.created_at, done_at=j.done_at,
        )
        for j, gname, telco in rows
    ]


@router.post("/jobs/retry", response_model=PurgeRetryResult)
def retry_failed(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """실패한 작업 중 경로가 있는 것(=API 쪽 실패)을 다시 대기열로. 경로 불일치는 설정을 고친 뒤 새 업로드로."""
    now = datetime.now(timezone.utc)
    n = (
        db.query(PurgeJob)
        .filter(PurgeJob.status == purge.STATUS_FAILED, PurgeJob.purge_path.isnot(None))
        .update({
            PurgeJob.status: purge.STATUS_PENDING, PurgeJob.attempts: 0,
            PurgeJob.next_try_at: now, PurgeJob.done_at: None,
        }, synchronize_session=False)
    )
    db.commit()
    return PurgeRetryResult(requeued=n)
