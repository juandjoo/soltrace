from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_user, require_admin
from app.models import Device, DeviceGroup, Group
from app.schemas import GroupCreate, GroupDeviceAssign, GroupResponse, GroupUpdate

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _to_response(group: Group, db: Session) -> GroupResponse:
    count = db.query(func.count(DeviceGroup.device_id)).filter(DeviceGroup.group_id == group.id).scalar()
    resp = GroupResponse.model_validate(group)
    resp.device_count = count or 0
    return resp


@router.get("", response_model=List[GroupResponse])
def list_groups(db: Session = Depends(get_db), user: Principal = Depends(get_current_user)):
    # 통신사 > 서비스 순: 소속 통신사로 묶고(미지정은 뒤로) 그 안에서 이름순
    q = db.query(Group)
    if not user.is_admin:
        # 고객 계정은 본인 customer 그룹만
        q = q.filter(Group.customer == (user.customer or ""))
    groups = q.order_by(Group.telco.nullslast(), Group.name).all()
    return [_to_response(g, db) for g in groups]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    group = Group(**body.model_dump())
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
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    db.commit()
    db.refresh(group)
    return _to_response(group, db)


@router.put("/{group_id}/devices", response_model=GroupResponse)
def assign_devices(
    group_id: int,
    body: GroupDeviceAssign,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """그룹에 소속될 장비를 한 번에 지정한다(전체 교체). 장비관리의 그룹 배정과 동일한 매핑을 반대 방향에서 편집."""
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    device_ids = list(dict.fromkeys(body.device_ids))
    if device_ids:
        found = db.query(Device.id).filter(Device.id.in_(device_ids)).count()
        if found != len(device_ids):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Some device IDs not found")

    db.query(DeviceGroup).filter(DeviceGroup.group_id == group_id).delete()
    for did in device_ids:
        db.add(DeviceGroup(device_id=did, group_id=group_id))
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
