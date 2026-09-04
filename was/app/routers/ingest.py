import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device, FtpLog
from app.schemas import (
    DeviceRegister, HeartbeatRequest, IngestResponse, LogBatch,
)
from app import write_buffer as wb

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

VALID_ACTIONS = {"upload", "download", "delete", "rename", "login", "logout", "mkdir", "rmdir", "cwd_fail"}


def _row_hash(entry: FtpLog) -> str:
    """row_hash — SQL backfill 수식 (app/migrations.py run_migrations)과 동일한 필드·순서."""
    lt = entry.log_time
    if lt and lt.tzinfo is None:
        lt = lt.replace(tzinfo=timezone.utc)
    key = "|".join([
        str(entry.device_id),
        lt.strftime("%Y-%m-%d %H:%M:%S") if lt else "",
        entry.username or "",
        entry.action or "",
        entry.file_path or "",
        str(entry.file_size or 0),
        entry.session_id or "",
        entry.client_ip or "",
    ])
    return hashlib.md5(key.encode()).hexdigest()


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(req: DeviceRegister, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_key == req.device_key).first()
    if device:
        device.hostname = req.hostname
        if req.ip_address:
            device.ip_address = req.ip_address
        if req.os_info:
            device.os_info = req.os_info
        if req.kernel_version:
            device.kernel_version = req.kernel_version
        if req.proftpd_version:
            device.proftpd_version = req.proftpd_version
        if req.daemon_version:
            device.daemon_version = req.daemon_version
        device.last_heartbeat = datetime.now(timezone.utc)
        db.commit()
        return {"device_id": device.id, "status": device.status, "registered": False}

    device = Device(
        hostname=req.hostname,
        ip_address=req.ip_address,
        device_key=req.device_key,
        os_info=req.os_info,
        kernel_version=req.kernel_version,
        proftpd_version=req.proftpd_version,
        daemon_version=req.daemon_version,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return {"device_id": device.id, "status": device.status, "registered": True}


@router.post("/heartbeat")
def heartbeat(req: HeartbeatRequest, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_key == req.device_key).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    device.last_heartbeat = datetime.now(timezone.utc)
    if req.hostname:
        device.hostname = req.hostname
    if req.ip_address:
        device.ip_address = req.ip_address

    # 데몬 상태 저장 (전송된 필드만 반영)
    _status_fields = (
        "daemon_status", "last_send_time", "buffer_lines", "queue_size",
        "consecutive_failures", "error_message", "cpu_percent",
        "mem_mb", "disk_free_gb", "daemon_uptime",
    )
    for field in _status_fields:
        val = getattr(req, field, None)
        if val is not None:
            setattr(device, field, val)

    resp: dict = {"status": device.status}
    if device.update_requested:
        resp["update"] = True
        device.update_requested = False
    db.commit()
    return resp


@router.post("/logs", response_model=IngestResponse)
def ingest_logs(batch: LogBatch, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_key == batch.device_key).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown device key")
    if device.status == "disabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device disabled")

    # 쓰기 버퍼가 밀려 있으면 받지 않는다. accepted 를 돌려주면 데몬이 자기 위치를
    # 넘겨 버리므로, 여기서 받아 놓고 못 쓰면 그 로그는 어디에도 남지 않는다.
    # 503 을 주면 데몬이 자기 디스크 버퍼에 담아 두었다가 다시 보낸다.
    buf = wb.get_buffer()
    if buf is not None and buf.saturated():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="쓰기 버퍼 포화 — 잠시 후 다시 보내주세요",
            headers={"Retry-After": "60"},
        )

    accepted = 0
    rejected = 0
    entries = []

    for entry in batch.logs:
        if entry.action not in VALID_ACTIONS:
            rejected += 1
            continue
        # CWD / : chroot 환경 클라이언트 초기화 루틴 노이즈 — 저장 제외
        if entry.action == "cwd_fail" and entry.file_path in ("/", "", None):
            rejected += 1
            continue
        obj = FtpLog(
            device_id=device.id,
            log_time=entry.log_time,
            client_ip=entry.client_ip,
            username=entry.username,
            action=entry.action,
            file_path=entry.file_path,
            file_size=entry.file_size,
            transfer_time=entry.transfer_time,
            transfer_type=entry.transfer_type,
            status=entry.status,
            session_id=entry.session_id,
        )
        obj.row_hash = _row_hash(obj)
        entries.append(obj)
        accepted += 1

    if entries:
        # 즉시 DB 쓰기 대신 버퍼(lifespan 에서 워커마다 초기화)에 넣어 워커 블로킹 제거
        buf.add(entries)

    return IngestResponse(accepted=accepted, rejected=rejected)
