from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_user, require_admin
from app.models import Device, DeviceGroup, Group, PurgeTarget
from app import daemon_update
from app.schemas import (
    DaemonUpdateGroups, DaemonUpdateResult, GroupCreate, GroupDeviceAssign, GroupResponse,
    GroupUpdate,
)

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _to_response(group: Group, db: Session) -> GroupResponse:
    count = db.query(func.count(DeviceGroup.device_id)).filter(DeviceGroup.group_id == group.id).scalar()
    resp = GroupResponse.model_validate(group)
    resp.device_count = count or 0
    return resp


def _replace_purge_targets(db: Session, group: Group, targets) -> None:
    """purge 대상 전체 교체. 같은 FTP 계정이 두 번 오면 422."""
    targets = targets or []
    names = [t.ftp_username.strip() for t in targets]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise HTTPException(status_code=422, detail=f"purge 대상 FTP 계정 중복: {', '.join(sorted(dup))}")
    if group.id is not None and group.purge_targets:
        # 한 flush 안에서는 INSERT 가 DELETE 보다 먼저 나가 같은 계정을 다시 넣으면
        # UNIQUE(group_id, ftp_username) 에 걸린다 — 지우기를 먼저 반영한다.
        group.purge_targets = []
        db.flush()
    group.purge_targets = [
        PurgeTarget(
            ftp_username=t.ftp_username.strip(),
            service=t.service.strip(),
            volume=t.volume.strip(),
            strip_prefix=(t.strip_prefix or "").strip().rstrip("/") or None,
        )
        for t in targets
    ]


@router.get("", response_model=List[GroupResponse])
def list_groups(db: Session = Depends(get_db), user: Principal = Depends(get_current_user)):
    # 통신사 > 서비스 순: 소속 통신사로 묶고(미지정은 뒤로) 그 안에서 이름순
    q = db.query(Group)
    if not user.is_admin:
        # 고객 계정은 본인 customer 그룹만
        q = q.filter(Group.customer == (user.customer or ""))
    groups = q.order_by(Group.telco.nullslast(), Group.name).all()
    resp = [_to_response(g, db) for g in groups]
    if not user.is_admin:
        for r in resp:
            r.purge_targets = []   # 운영 설정 — 고객 화면에는 쓸 데가 없다
    return resp


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    data = body.model_dump(exclude={"purge_targets"})
    group = Group(**data)
    _replace_purge_targets(db, group, body.purge_targets)
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
    for field, value in body.model_dump(exclude_unset=True, exclude={"purge_targets"}).items():
        setattr(group, field, value)
    if "purge_targets" in body.model_fields_set:
        _replace_purge_targets(db, group, body.purge_targets)
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


@router.post("/{group_id}/update", response_model=DaemonUpdateResult)
def request_group_daemon_update(
    group_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """그룹에 속한 장비의 데몬 자가 업데이트를 한 번에 요청한다.

    장비를 하나씩 누르는 것(POST /devices/{id}/update)과 **같은 플래그**를 세울 뿐이다.
    적용은 각 장비의 다음 하트비트에서 데몬이 스스로 한다.
    """
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    return DaemonUpdateResult(requested=daemon_update.for_groups(db, [group_id]))


@router.post("/bulk-update", response_model=DaemonUpdateResult)
def request_group_daemon_update_bulk(
    body: DaemonUpdateGroups,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """체크한 그룹들의 장비 전부에 한꺼번에 업데이트를 요청한다.

    두 그룹에 겹쳐 속한 장비는 한 번만 센다.
    """
    return DaemonUpdateResult(requested=daemon_update.for_groups(db, body.group_ids))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    db.delete(group)
    db.commit()
