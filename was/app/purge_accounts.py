"""업로드 자동 purge 대상 계정 — 그룹 설정(groups.purge_*) → 장비 데몬.

purge 자체는 FTP 장비의 데몬이 한다(ftp-daemon/soltrace_purge.py). WAS 는 "어느 계정의
업로드를 purge 할지"만 정해 하트비트 응답으로 내려준다. 경로 규칙과 PURGE 대상 서버 IP 는
장비 config.ini [purge] 에서 따로 관리한다.
"""
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import DeviceGroup, Group

# 줄바꿈·쉼표·공백 어느 것으로 나눠 붙여넣어도 받는다 (FTP 계정에는 공백이 없다)
_SPLIT = re.compile(r"[\s,]+")


def parse_accounts(text: Optional[str]) -> List[str]:
    """입력 문자열을 계정 목록으로. 순서는 지키고 중복은 뺀다."""
    if not text:
        return []
    return list(dict.fromkeys(a for a in _SPLIT.split(text) if a))


def normalize_accounts(text: Optional[str]) -> Optional[str]:
    """저장용 — 한 줄에 하나. 비면 None."""
    accounts = parse_accounts(text)
    return "\n".join(accounts) if accounts else None


def accounts_for_device(db: Session, device_id: int) -> List[str]:
    """장비가 속한 그룹 중 purge 를 켠 그룹들의 계정 합집합."""
    rows = (
        db.query(Group.purge_accounts)
        .join(DeviceGroup, DeviceGroup.group_id == Group.id)
        .filter(DeviceGroup.device_id == device_id, Group.purge_enabled.is_(True))
        .order_by(Group.id)
        .all()
    )
    merged: List[str] = []
    for (text,) in rows:
        merged.extend(parse_accounts(text))
    return list(dict.fromkeys(merged))
