from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, DeviceGroup, Group
from app.schemas import GroupCreate, GroupResponse, GroupUpdate

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _to_response(group: Group, db: Session) -> GroupResponse:
    count = db.query(func.count(DeviceGroup.device_id)).filter(DeviceGroup.group_id == group.id).scalar()
    resp = GroupResponse.model_validate(group)
    resp.device_count = count or 0
    return resp


@router.get("", response_model=List[GroupResponse])
def list_groups(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    groups = db.query(Group).order_by(Group.group_type, Group.name).all()
    return [_to_response(g, db) for g in groups]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    data = body.model_dump()
    if data.get("group_type") != "telco":
        data["telco"] = None      # telco 유형이 아니면 통신사 비움
    group = Group(**data)
    db.add(group)
    db.commit()
    db.refresh(group)
    return _to_response(group, db)


@router.put("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    body: GroupUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if body.name and body.name != group.name:
        if db.query(Group).filter(Group.name == body.name).first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(group, field, value)
    if group.group_type != "telco":
        group.telco = None        # telco 유형이 아니면 통신사 비움
    db.commit()
    db.refresh(group)
    return _to_response(group, db)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    db.delete(group)
    db.commit()
