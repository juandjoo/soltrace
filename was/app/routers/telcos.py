from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Group, PurgeTarget, Telco
from app.schemas import TelcoApiUpdate, TelcoCreate, TelcoItem

router = APIRouter(prefix="/api/v1/telcos", tags=["telcos"])


def _get(db: Session, telco_id: int) -> Telco:
    telco = db.query(Telco).filter(Telco.id == telco_id).first()
    if not telco:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="통신사를 찾을 수 없습니다")
    return telco


def _clean(value):
    """붙여넣기에 섞여 오는 공백·NBSP·탭 제거. 비면 None."""
    value = (value or "").strip()
    return value or None


@router.get("", response_model=List[TelcoItem])
def list_telcos(db: Session = Depends(get_db), _: str = Depends(require_admin)):
    return db.query(Telco).order_by(Telco.name).all()


@router.post("", response_model=TelcoItem, status_code=status.HTTP_201_CREATED)
def create_telco(body: TelcoCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    name = body.name.strip()
    if db.query(Telco).filter(Telco.name == name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 통신사입니다")
    telco = Telco(name=name)
    db.add(telco)
    db.commit()
    db.refresh(telco)
    return telco


@router.put("/{telco_id}/api", response_model=TelcoItem)
def update_telco_api(telco_id: int, body: TelcoApiUpdate,
                     db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """CDN API 주소 저장. 계정·비밀번호는 그룹마다 넣는다(그룹 관리)."""
    telco = _get(db, telco_id)
    auth_url, purge_url = _clean(body.api_auth_url), _clean(body.api_purge_url)
    for label, url in (("토큰 발급 URL", auth_url), ("purge URL", purge_url)):
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail=f"{label} 은 http:// 또는 https:// 로 시작해야 합니다")
    if purge_url and ("{service}" not in purge_url or "{volume}" not in purge_url):
        raise HTTPException(status_code=422, detail="purge URL 에 {service} 와 {volume} 자리표시자가 있어야 합니다")
    if auth_url != telco.api_auth_url:
        # 발급처가 바뀌면 그 통신사 그룹의 업로드 ID 들이 들고 있던 토큰은 못 쓴다 — 다음 호출 때 새로 받는다
        group_ids = db.query(Group.id).filter(Group.telco == telco.name)
        db.query(PurgeTarget).filter(PurgeTarget.group_id.in_(group_ids)).update(
            {PurgeTarget.api_token: None, PurgeTarget.api_token_at: None}, synchronize_session=False)
    telco.api_auth_url = auth_url
    telco.api_purge_url = purge_url
    db.commit()
    db.refresh(telco)
    return telco


@router.delete("/{telco_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_telco(telco_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    telco = _get(db, telco_id)
    db.delete(telco)
    db.commit()
