import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, FtpLog
from app.schemas import FtpLogResponse, LogListResponse

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

DEFAULT_RANGE_DAYS = 30


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
    # 기간 미지정 시 최근 30일로 제한 — 전체 테이블 COUNT 방지
    if start_time is None and end_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)

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
    # 기간 미지정 시 최근 30일로 제한
    if start_time is None and end_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "device", "log_time", "client_ip", "username",
                         "action", "file_path", "file_size", "transfer_time", "status"])
        yield buf.getvalue()

        # 튜플 쿼리로 ORM 객체 생성 없이 yield_per 스트리밍
        q = (
            db.query(
                FtpLog.id, Device.hostname, FtpLog.log_time,
                FtpLog.client_ip, FtpLog.username, FtpLog.action,
                FtpLog.file_path, FtpLog.file_size, FtpLog.transfer_time, FtpLog.status,
            )
            .join(Device, FtpLog.device_id == Device.id)
        )
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

        for row in q.order_by(FtpLog.log_time.desc()).yield_per(1000):
            buf.seek(0)
            buf.truncate(0)
            writer.writerow([
                row.id,
                row.hostname or "",
                row.log_time.isoformat() if row.log_time else "",
                row.client_ip or "",
                row.username or "",
                row.action,
                row.file_path or "",
                row.file_size,
                row.transfer_time,
                row.status,
            ])
            yield buf.getvalue()

    filename = f"ftp_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/xlsx")
def export_xlsx(
    device_id: Optional[int] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    if start_time is None and end_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("FTP Logs")
    ws.freeze_panes = "A2"

    # 헤더 (굵게)
    headers = ["ID", "장비", "일시", "클라이언트 IP", "사용자", "작업",
               "파일 경로", "파일 크기(bytes)", "전송시간(s)", "상태"]
    bold = Font(bold=True)
    header_row = [WriteOnlyCell(ws, value=h) for h in headers]
    for cell in header_row:
        cell.font = bold
    ws.append(header_row)

    q = (
        db.query(
            FtpLog.id, Device.hostname, FtpLog.log_time,
            FtpLog.client_ip, FtpLog.username, FtpLog.action,
            FtpLog.file_path, FtpLog.file_size, FtpLog.transfer_time, FtpLog.status,
        )
        .join(Device, FtpLog.device_id == Device.id)
    )
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

    for row in q.order_by(FtpLog.log_time.desc()).yield_per(1000):
        # Excel은 timezone-aware datetime을 지원하지 않으므로 UTC naive로 변환
        log_time = row.log_time.replace(tzinfo=None) if row.log_time else None
        ws.append([
            row.id, row.hostname or "", log_time,
            row.client_ip or "", row.username or "", row.action,
            row.file_path or "", row.file_size, row.transfer_time, row.status,
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"ftp_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
