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
from app.deps import device_scope
from app.models import Device, DeviceGroup, FtpLog
from app.schemas import FtpLogResponse, LogListResponse

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

DEFAULT_RANGE_DAYS = 90


def _apply_filters(
    q,
    db: Session,
    *,
    device_id: Optional[int],
    group_id: Optional[int],
    username: Optional[str],
    client_ip: Optional[str],
    file_path: Optional[str],
    action: Optional[str],
    exclude_actions: Optional[str],
    log_status: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    allowed_ids: Optional[list[int]] = None,
):
    # 테넌트 격리: 고객 계정이면 본인 device 로만 제한 (admin 은 None → 제한 없음)
    if allowed_ids is not None:
        q = q.filter(FtpLog.device_id.in_(allowed_ids))
    if device_id:
        q = q.filter(FtpLog.device_id == device_id)
    if group_id:
        members = db.query(DeviceGroup.device_id).filter(DeviceGroup.group_id == group_id)
        q = q.filter(FtpLog.device_id.in_(members))
    if username:
        q = q.filter(FtpLog.username.ilike(f"%{username}%"))
    if client_ip:
        q = q.filter(FtpLog.client_ip.ilike(f"%{client_ip}%"))
    if file_path:
        q = q.filter(FtpLog.file_path.ilike(f"%{file_path}%"))
    if action:
        q = q.filter(FtpLog.action == action)
    elif exclude_actions:
        q = q.filter(FtpLog.action.notin_(exclude_actions.split(",")))
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
    group_id: Optional[int] = None,
    username: Optional[str] = None,
    client_ip: Optional[str] = None,
    file_path: Optional[str] = None,
    action: Optional[str] = None,
    exclude_actions: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
):
    # 기간 미지정 시 최근 30일로 제한 — 전체 테이블 COUNT 방지
    if start_time is None and end_time is None:
        start_time = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)

    q = _apply_filters(
        db.query(FtpLog), db,
        device_id=device_id, group_id=group_id, username=username, client_ip=client_ip,
        file_path=file_path, action=action, exclude_actions=exclude_actions, log_status=log_status,
        start_time=start_time, end_time=end_time, allowed_ids=scope,
    )
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
        r.device_ip = log.device.ip_address if log.device else None
        results.append(r)

    return LogListResponse(total=total, page=page, size=size, items=results)


@router.get("/export")
def export_csv(
    device_id: Optional[int] = None,
    group_id: Optional[int] = None,
    username: Optional[str] = None,
    client_ip: Optional[str] = None,
    file_path: Optional[str] = None,
    action: Optional[str] = None,
    exclude_actions: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
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
        q = _apply_filters(
            q, db,
            device_id=device_id, group_id=group_id, username=username, client_ip=client_ip,
            file_path=file_path, action=action, exclude_actions=exclude_actions, log_status=log_status,
            start_time=start_time, end_time=end_time, allowed_ids=scope,
        )

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
    group_id: Optional[int] = None,
    username: Optional[str] = None,
    client_ip: Optional[str] = None,
    file_path: Optional[str] = None,
    action: Optional[str] = None,
    exclude_actions: Optional[str] = None,
    log_status: Optional[str] = Query(default=None, alias="status"),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
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
    q = _apply_filters(
        q, db,
        device_id=device_id, group_id=group_id, username=username, client_ip=client_ip,
        file_path=file_path, action=action, exclude_actions=exclude_actions, log_status=log_status,
        start_time=start_time, end_time=end_time, allowed_ids=scope,
    )

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
