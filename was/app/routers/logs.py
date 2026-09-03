import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import device_scope
from app.models import Device, DeviceGroup, FtpLog
from app.service_monitor import cwd_probe_sql
from app.schemas import FtpLogResponse, LogCountResponse, LogListResponse

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

DEFAULT_RANGE_DAYS = 90


class LogFilters:
    """목록·총건수·CSV·XLSX 가 공유하는 조회 조건. FastAPI 가 쿼리스트링에서 주입한다.

    기간 미지정 시 최근 DEFAULT_RANGE_DAYS 로 제한 — 전체 테이블 스캔 방지.
    네 엔드포인트의 필터 규칙이 갈라지지 않도록 여기 한 곳에서만 정의한다.
    """

    def __init__(
        self,
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
    ):
        if start_time is None and end_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RANGE_DAYS)
        self.device_id = device_id
        self.group_id = group_id
        self.username = username
        self.client_ip = client_ip
        self.file_path = file_path
        self.action = action
        self.exclude_actions = exclude_actions
        self.log_status = log_status
        self.start_time = start_time
        self.end_time = end_time

    def apply(self, q, db: Session, allowed_ids: Optional[list[int]]):
        # 테넌트 격리: 고객 계정이면 본인 device 로만 제한 (admin 은 None → 제한 없음)
        if allowed_ids is not None:
            q = q.filter(FtpLog.device_id.in_(allowed_ids))
        if self.device_id:
            q = q.filter(FtpLog.device_id == self.device_id)
        if self.group_id:
            members = db.query(DeviceGroup.device_id).filter(DeviceGroup.group_id == self.group_id)
            q = q.filter(FtpLog.device_id.in_(members))
        if self.username:
            q = q.filter(FtpLog.username.ilike(f"%{self.username}%"))
        if self.client_ip:
            q = q.filter(FtpLog.client_ip.ilike(f"%{self.client_ip}%"))
        if self.file_path:
            q = q.filter(FtpLog.file_path.ilike(f"%{self.file_path}%"))
        if self.action:
            q = q.filter(FtpLog.action == self.action)
        elif self.exclude_actions:
            q = q.filter(FtpLog.action.notin_(self.exclude_actions.split(",")))
        if self.log_status:
            q = q.filter(FtpLog.status == self.log_status)
        if self.start_time:
            q = q.filter(FtpLog.log_time >= self.start_time)
        if self.end_time:
            q = q.filter(FtpLog.log_time <= self.end_time)
        return self._hide_cwd_probes(q)

    def _hide_cwd_probes(self, q):
        """존재 확인(CWD 실패 직후 그 경로가 생성된 건)은 목록에서도 감춘다.

        폴더로 이동하다 실패한 게 아니라 업로드 전에 떠본 것이라 '실패'로 보여줄 게 아니다.
        집계·알림과 같은 판정(service_monitor.cwd_probe_sql)을 쓴다.
        cwd_fail 행에만 EXISTS 가 붙도록 action 비교를 앞에 두어 다른 행은 그대로 통과한다.
        """
        # mkdir 쪽 파티션 프루닝용 절대 구간 — 기간을 지정하지 않은 조회에도 항상 채운다.
        until = self.end_time or datetime.now(timezone.utc)
        since = self.start_time or (until - timedelta(days=DEFAULT_RANGE_DAYS))
        # 괄호 필수 — OR 이 다른 필터(기간·장비·사용자)까지 삼키면 조회 결과가 통째로 어긋난다.
        cond = text(
            f"(ftp_logs.action <> 'cwd_fail'"
            f" OR NOT {cwd_probe_sql('ftp_logs', ':probe_since', ':probe_until')})"
        ).bindparams(probe_since=since, probe_until=until)
        return q.filter(cond)


# CSV/XLSX 내보내기 공통 컬럼 — ORM 객체 생성 없이 튜플로 스트리밍
_EXPORT_COLS = (
    FtpLog.id, Device.hostname, FtpLog.log_time,
    FtpLog.client_ip, FtpLog.username, FtpLog.action,
    FtpLog.file_path, FtpLog.file_size, FtpLog.transfer_time, FtpLog.status,
)


def _export_rows(db: Session, f: LogFilters, scope: Optional[list[int]]):
    q = db.query(*_EXPORT_COLS).join(Device, FtpLog.device_id == Device.id)
    return f.apply(q, db, scope).order_by(FtpLog.log_time.desc()).yield_per(1000)


def _export_filename(ext: str) -> str:
    return f"ftp_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{ext}"


@router.get("", response_model=LogListResponse)
def query_logs(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    f: LogFilters = Depends(),
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
):
    # 총건수는 /logs/count 가 별도로 정확히 센다. 대량 구간에서 COUNT 가 수 초 걸려도
    # 첫 페이지는 즉시 내려가도록 여기서는 세지 않는다.
    items = (
        f.apply(db.query(FtpLog), db, scope)
        .join(Device, FtpLog.device_id == Device.id)
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

    return LogListResponse(page=page, size=size, items=results)


@router.get("/count", response_model=LogCountResponse)
def count_logs(
    f: LogFilters = Depends(),
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
):
    """목록과 동일한 필터로 정확한 총건수를 센다 (상한·추정 없음).

    프론트가 목록 요청과 병렬로 호출하고, 같은 필터로 페이지만 바꿀 때는 재호출하지 않는다.
    """
    total = f.apply(db.query(FtpLog), db, scope).with_entities(func.count()).scalar()
    return LogCountResponse(total=total or 0)


@router.get("/export")
def export_csv(
    f: LogFilters = Depends(),
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
):
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "device", "log_time", "client_ip", "username",
                         "action", "file_path", "file_size", "transfer_time", "status"])
        yield buf.getvalue()

        for row in _export_rows(db, f, scope):
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

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={_export_filename('csv')}"},
    )


@router.get("/export/xlsx")
def export_xlsx(
    f: LogFilters = Depends(),
    db: Session = Depends(get_db),
    scope: Optional[list[int]] = Depends(device_scope),
):
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

    for row in _export_rows(db, f, scope):
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

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={_export_filename('xlsx')}"},
    )
