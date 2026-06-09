import csv
import io
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, FtpLog
from app.schemas import FtpLogResponse, LogListResponse

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


def _build_query(
    db: Session,
    device_id: Optional[int],
    username: Optional[str],
    action: Optional[str],
    log_status: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
):
    q = db.query(FtpLog)
    if device_id:
        q = q.filter(FtpLog.device_id == device_id)
    if username:
        q = q.filter(FtpLog.username.ilike(f"%{username}%"))
    if action:
        q = q.filter(FtpLog.action == action)
    if log_status:
        q = q.filter(FtpLog.status == log_status)
    if start_time:
        q = q.filter(FtpLog.log_time >= start_time)
    if end_time:
        q = q.filter(FtpLog.log_time <= end_time)
    return q


@router.get("", response_model=LogListResponse)
def query_logs(
    device_id: Optional[int] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    q = _build_query(db, device_id, username, action, log_status, start_time, end_time)
    total = q.with_entities(func.count()).scalar()
    items = (
        q.join(Device, FtpLog.device_id == Device.id)
        .order_by(FtpLog.log_time.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    results = []
    for log in items:
        r = FtpLogResponse.model_validate(log)
        r.device_hostname = log.device.hostname if log.device else None
        results.append(r)

    return LogListResponse(total=total, page=page, size=size, items=results)


@router.get("/export")
def export_csv(
    device_id: Optional[int] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    q = _build_query(db, device_id, username, action, log_status, start_time, end_time)
    items = q.join(Device).order_by(FtpLog.log_time.desc()).limit(50000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "device", "log_time", "client_ip", "username",
                     "action", "file_path", "file_size", "transfer_time", "status"])
    for log in items:
        writer.writerow([
            log.id,
            log.device.hostname if log.device else "",
            log.log_time.isoformat() if log.log_time else "",
            log.client_ip or "",
            log.username or "",
            log.action,
            log.file_path or "",
            log.file_size,
            log.transfer_time,
            log.status,
        ])

    output.seek(0)
    filename = f"ftp_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
