from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Telco
from app.schemas import TelcoCreate, TelcoItem

router = APIRouter(prefix="/api/v1/telcos", tags=["telcos"])


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


@router.delete("/{telco_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_telco(telco_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    telco = db.query(Telco).filter(Telco.id == telco_id).first()
    if not telco:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="통신사를 찾을 수 없습니다")
    db.delete(telco)
    db.commit()
