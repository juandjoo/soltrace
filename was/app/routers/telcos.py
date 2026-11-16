from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Telco
from app import purge
from app.schemas import TelcoApiUpdate, TelcoCreate, TelcoItem, TelcoTokenTest

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
    """CDN API 정보 저장. 계정·주소가 바뀌면 기존 토큰은 버린다(다음 호출 때 새로 받는다)."""
    telco = _get(db, telco_id)
    for label, url in (("인증 URL", body.api_auth_url), ("purge URL", body.api_purge_url)):
        if _clean(url) and not _clean(url).startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail=f"{label} 은 http:// 또는 https:// 로 시작해야 합니다")
    purge_url = _clean(body.api_purge_url)
    if purge_url and ("{service}" not in purge_url or "{volume}" not in purge_url):
        raise HTTPException(status_code=422, detail="purge URL 에 {service} 와 {volume} 자리표시자가 있어야 합니다")

    before = (telco.api_auth_url, telco.api_username, telco.api_password)
    telco.api_auth_url = _clean(body.api_auth_url)
    telco.api_purge_url = purge_url
    telco.api_username = _clean(body.api_username)
    if _clean(body.api_password):
        telco.api_password = _clean(body.api_password)
    if (telco.api_auth_url, telco.api_username, telco.api_password) != before:
        telco.api_token = None
        telco.api_token_at = None
    db.commit()
    db.refresh(telco)
    return telco


@router.post("/{telco_id}/api/token-test", response_model=TelcoTokenTest)
def test_telco_token(telco_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """저장된 계정으로 토큰을 실제로 발급해 본다. 성공하면 그 토큰을 그대로 쓴다."""
    telco = _get(db, telco_id)
    try:
        token = purge.issue_token(db, telco)
    except purge.PurgeApiError as e:
        db.rollback()
        return TelcoTokenTest(ok=False, message=str(e))
    db.commit()
    return TelcoTokenTest(
        ok=True,
        message=f"토큰 발급 성공 (길이 {len(token)}자)",
        api_token_at=telco.api_token_at,
    )


@router.delete("/{telco_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_telco(telco_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    telco = _get(db, telco_id)
    db.delete(telco)
    db.commit()
