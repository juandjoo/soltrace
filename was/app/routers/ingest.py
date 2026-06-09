from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device, FtpLog
from app.schemas import (
    DeviceRegister, HeartbeatRequest, IngestResponse, LogBatch,
)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

VALID_ACTIONS = {"upload", "download", "delete", "rename", "login", "logout", "mkdir", "rmdir"}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(req: DeviceRegister, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_key == req.device_key).first()
    if device:
        device.hostname = req.hostname
        if req.ip_address:
            device.ip_address = req.ip_address
        if req.os_info:
            device.os_info = req.os_info
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
    db.commit()
    return {"status": device.status}


@router.post("/logs", response_model=IngestResponse)
def ingest_logs(batch: LogBatch, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_key == batch.device_key).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown device key")
    if device.status == "disabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device disabled")

    accepted = 0
    rejected = 0
    entries = []

    for entry in batch.logs:
        if entry.action not in VALID_ACTIONS:
            rejected += 1
            continue
        entries.append(FtpLog(
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
        ))
        accepted += 1

    if entries:
        db.bulk_save_objects(entries)
        db.commit()

    return IngestResponse(accepted=accepted, rejected=rejected)
