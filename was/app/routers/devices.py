from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, DeviceGroup, Group
from app.schemas import DeviceConfirm, DeviceGroupAssign, DeviceResponse

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.get("", response_model=List[DeviceResponse])
def list_devices(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    q = db.query(Device)
    if status_filter:
        q = q.filter(Device.status == status_filter)
    return q.order_by(Device.created_at.desc()).all()


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.put("/{device_id}/status", response_model=DeviceResponse)
def update_device_status(
    device_id: int,
    body: DeviceConfirm,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    device.status = body.status
    db.commit()
    db.refresh(device)
    return device


@router.put("/{device_id}/groups", response_model=DeviceResponse)
def assign_groups(
    device_id: int,
    body: DeviceGroupAssign,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    # Validate groups exist
    if body.group_ids:
        groups = db.query(Group).filter(Group.id.in_(body.group_ids)).all()
        if len(groups) != len(body.group_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Some group IDs not found")

    db.query(DeviceGroup).filter(DeviceGroup.device_id == device_id).delete()
    for gid in body.group_ids:
        db.add(DeviceGroup(device_id=device_id, group_id=gid))
    db.commit()
    db.refresh(device)
    return device


@router.post("/{device_id}/update", status_code=status.HTTP_204_NO_CONTENT)
def request_daemon_update(
    device_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """다음 하트비트에서 데몬 자가 업데이트를 트리거한다."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    device.update_requested = True
    db.commit()


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db.delete(device)
    db.commit()
