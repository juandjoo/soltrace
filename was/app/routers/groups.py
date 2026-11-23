from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, get_current_user, require_admin
from app.models import DeviceGroup, Group, PurgeTarget, Telco
from app import backfill
from app import purge
from app import daemon_update
from app.schemas import (
    BackfillJobResponse, BackfillRequest, BackfillRequestResult,
    DaemonUpdateResult, GroupCreate, GroupResponse,
    GroupUpdate, TokenTestRequest, TokenTestResult,
)

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _to_response(group: Group, db: Session) -> GroupResponse:
    count = db.query(func.count(DeviceGroup.device_id)).filter(DeviceGroup.group_id == group.id).scalar()
    resp = GroupResponse.model_validate(group)
    resp.device_count = count or 0
    return resp


def _clean(value):
    """붙여넣기에 섞여 오는 공백·NBSP·탭 제거. 비면 None."""
    value = (value or "").strip()
    return value or None


def _mount_key(strip_prefix) -> str:
    """마운트 경로 비교용 — 화면 입력의 끝 '/' 와 공백 차이를 같은 것으로 본다."""
    return (strip_prefix or "").strip().rstrip("/")


def _stored_password(rows, api_username):
    """같은 API 계정으로 이미 저장된 비밀번호 — 같은 계정에 마운트 경로를 추가할 때 다시 묻지 않는다."""
    if not api_username:
        return None
    return next((r.api_password for r in rows if r.api_username == api_username and r.api_password), None)


def _sync_purge_targets(group: Group, targets, telco_changed: bool) -> None:
    """업로드 ID 목록을 맞춘다 — 같은 행(id, 없으면 FTP ID+마운트 경로)은 고치고(비밀번호·토큰 유지), 빠진 행은 지운다.

    같은 FTP ID 도 마운트 경로가 다르면 따로 둔다(경로별로 다른 서비스·볼륨으로 purge).
    전체를 지우고 다시 넣으면 화면이 다시 보내지 않는 비밀번호와 발급받은 토큰이 사라진다.
    비밀번호 칸을 비우면 그 행에 저장된 값, 없으면 같은 API 계정에 저장된 값을 쓴다.
    계정·비밀번호·통신사가 바뀐 행의 토큰은 버린다.
    """
    targets = targets or []
    keys = [(t.ftp_username.strip(), _mount_key(t.strip_prefix)) for t in targets]
    dup = sorted({f"{n} {m or '/'}" for n, m in keys if keys.count((n, m)) > 1})
    if dup:
        raise HTTPException(status_code=422, detail=f"purge 대상 FTP ID·마운트 경로 중복: {', '.join(dup)}")
    current = list(group.purge_targets)
    by_id = {t.id: t for t in current}
    by_key = {(t.ftp_username, _mount_key(t.strip_prefix)): t for t in current}
    # 화면이 돌려준 id 가 이 그룹 것일 때만 그 행을 고친다(마운트 경로를 바꿔도 비밀번호·토큰 유지).
    # id 로 이미 짝지은 행은 FTP ID+경로로 다시 짝짓지 않는다 — 한 행을 두 카드가 나눠 갖지 않게
    claimed = {t.id for t in targets if t.id in by_id}
    synced = []
    for t, key in zip(targets, keys):
        fallback = by_key.get(key)
        if fallback is not None and fallback.id in claimed:
            fallback = None
        row = by_id.get(t.id) or fallback or PurgeTarget()
        before = (row.api_username, row.api_password)
        row.ftp_username = key[0]
        row.enabled = t.enabled
        row.service = t.service.strip()
        row.volume = t.volume.strip()
        row.strip_prefix = key[1] or None
        row.api_username = _clean(t.api_username)
        if _clean(t.api_password):
            row.api_password = _clean(t.api_password)
        if row.api_username is None:
            row.api_password = None    # 계정을 지우면 비밀번호도 같이
        if telco_changed or (row.api_username, row.api_password) != before:
            row.api_token = None
            row.api_token_at = None
        synced.append(row)
    # 비밀번호 없는 행은 같은 API 계정의 저장값을 쓴다 — 이번에 함께 들어온 카드도 본다(카드 순서 무관)
    for row in synced:
        if row.api_username and not row.api_password:
            row.api_password = _stored_password(synced + current, row.api_username)
    group.purge_targets = synced      # 빠진 행은 delete-orphan 으로 지워진다


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
            # 운영 설정 — 고객 화면에는 쓸 데가 없다
            r.purge_targets = []
    return resp


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(body: GroupCreate, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    data = body.model_dump(exclude={"purge_targets"})
    group = Group(**data)
    _sync_purge_targets(group, body.purge_targets, telco_changed=False)
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
    old_telco = group.telco
    for field, value in body.model_dump(exclude_unset=True, exclude={"purge_targets"}).items():
        if field == "purge_active" and value is None:
            continue    # NOT NULL — 명시적 null 은 '안 바꿈'
        setattr(group, field, value)
    telco_changed = group.telco != old_telco
    if "purge_targets" in body.model_fields_set:
        _sync_purge_targets(group, body.purge_targets, telco_changed)
    elif telco_changed:
        # 통신사가 바뀌면 발급처가 달라진다 — 들고 있던 토큰은 못 쓴다
        for t in group.purge_targets:
            t.api_token, t.api_token_at = None, None
    db.commit()
    db.refresh(group)
    return _to_response(group, db)


@router.post("/token-test", response_model=TokenTestResult)
def test_group_token(body: TokenTestRequest, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """그룹 모달 업로드 ID 행의 '토큰 테스트' — 입력 중인 통신사·계정으로 실제 발급해 본다(저장하지 않음).

    비밀번호를 비우면 그 행(target_id), 없으면 그 그룹에서 같은 API 계정에 저장된 비밀번호를 쓴다
    (수정 화면은 비밀번호를 다시 보여주지 않는다).
    """
    telco = db.query(Telco).filter(Telco.name == body.telco).first()
    if telco is None or not telco.api_auth_url:
        return TokenTestResult(ok=False, message=f"통신사 '{body.telco}' 에 토큰 발급 URL 이 없습니다 (설정 › 텔코 정보)")
    password = _clean(body.api_password)
    if not password and body.group_id:
        rows = db.query(PurgeTarget).filter(PurgeTarget.group_id == body.group_id).all()
        own = next((r for r in rows if r.id == body.target_id), None)
        password = (own.api_password if own else None) or _stored_password(rows, _clean(body.api_username))
    try:
        token = purge.request_token(telco.api_auth_url, _clean(body.api_username), password)
    except purge.PurgeApiError as e:
        return TokenTestResult(ok=False, message=str(e))
    return TokenTestResult(ok=True, message=f"토큰 발급 성공 (길이 {len(token)}자)")


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


def _get_group(db: Session, group_id: int) -> Group:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.post("/{group_id}/backfill", response_model=BackfillRequestResult)
def request_group_backfill(
    group_id: int,
    body: BackfillRequest,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_admin),
):
    """그룹 장비들에 로그 재전송을 요청한다 — 장비 디스크에 남은 로그 중 WAS 에 없는 행만 들어온다.

    각 장비의 다음 하트비트에서 시작하고, 전체에서 동시에 backfill.MAX_RUNNING 대씩 돈다.
    """
    _get_group(db, group_id)
    try:
        r = backfill.request_for_group(db, group_id, body.date_from, body.date_to, user.username,
                                       body.transfer_pattern, body.extended_pattern)
    except backfill.BackfillRequestError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return BackfillRequestResult(**r)


@router.get("/{group_id}/backfill", response_model=List[BackfillJobResponse])
def list_group_backfill(group_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """그룹 장비별 가장 최근 재전송 작업."""
    _get_group(db, group_id)
    jobs = backfill.latest_for_group(db, group_id)
    out = []
    for j in jobs:
        item = BackfillJobResponse.model_validate(j, from_attributes=True)
        item.hostname = j.device.hostname if j.device else None
        out.append(item)
    return out


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    db.delete(group)
    db.commit()
