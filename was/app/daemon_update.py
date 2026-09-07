"""데몬 자가 업데이트 요청 — 단건·다건·그룹이 모두 이 한 곳을 쓴다.

세우는 것은 `Device.update_requested` 플래그 하나뿐이고, 실제 적용은 각 장비의 다음
하트비트에서 데몬이 스스로 한다(`app/routers/ingest.py` heartbeat).

경로마다 따로 세우면 '어느 화면에서 눌렀는지'에 따라 대상 조건이나 커밋 시점이 갈린다.
장비 하나를 누르든, 여러 대를 골라 누르든, 그룹째로 누르든 결과가 같아야 한다.
"""
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import Device, DeviceGroup


def _flag(db: Session, id_filter) -> int:
    n = (
        db.query(Device)
        .filter(id_filter)
        .update({Device.update_requested: True}, synchronize_session=False)
    )
    db.commit()
    return n


def for_devices(db: Session, device_ids: Iterable[int]) -> int:
    """지정한 장비들에 업데이트를 요청한다. 실제로 플래그가 선 장비 수를 돌려준다."""
    ids = list(dict.fromkeys(device_ids))
    if not ids:
        return 0
    return _flag(db, Device.id.in_(ids))


def for_groups(db: Session, group_ids: Iterable[int]) -> int:
    """지정한 그룹들에 속한 장비 전부에 업데이트를 요청한다.

    두 그룹에 겹쳐 속한 장비는 한 번만 센다 — UPDATE 가 매핑이 아니라 장비 행을 고른다.
    """
    ids = list(dict.fromkeys(group_ids))
    if not ids:
        return 0
    member_ids = db.query(DeviceGroup.device_id).filter(DeviceGroup.group_id.in_(ids))
    return _flag(db, Device.id.in_(member_ids))
