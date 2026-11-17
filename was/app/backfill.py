"""로그 재전송(backfill) — 웹에서 요청하면 장비가 디스크에 남은 로그를 다시 올려 보낸다.

예전에는 장비에 접속해 `soltrace_bulk.py` 를 손으로 돌려야 했다. 이제 그룹 관리에서 기간을
골라 요청하면, 장비별 작업 행(`backfill_jobs`)이 생기고 각 장비의 하트비트 응답으로
지시가 내려간다. 데몬은 bulk 를 **별도 프로세스**로 띄우고(파서의 세션 상태가 실시간 수집과
섞이지 않게), bulk 가 진행 상황을 직접 `/ingest/backfill/progress` 로 보고한다.

이미 들어와 있는 행은 WAS 의 `(device_id, log_time, row_hash)` 유니크 인덱스가 걸러내므로
같은 기간을 다시 보내도 중복되지 않는다 — 빠진 행만 새로 들어간다.

상태 흐름: queued → sent → running → done | failed.  요청 시점에 대상이 아니면 skipped.
상태는 메모리가 아니라 DB 에 둔다 — 워커가 여러 개이고 배포로 재기동돼도 같은 상태를 봐야 한다.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import retention
from app.gitinfo import version_at_least
from app.models import BackfillJob, Device, DeviceGroup

log = logging.getLogger("soltrace.backfill")

MAX_RUNNING = 2                # 전체에서 동시에 보내는 장비 수 — 쓰기 버퍼 포화 방지
MIN_DAEMON_VERSION = "1.3.0"   # 하트비트의 backfill 지시를 알아듣는 첫 데몬 버전
# 지시를 내려보냈는데 bulk 가 시작 보고를 안 하면(데몬 재시작·구버전) 슬롯을 비운다
SENT_TIMEOUT = timedelta(minutes=10)
# 진행 보고가 끊긴 지 이만큼 지나면 실패로 본다. bulk 는 WAS 가 바빠 기다리는 중에도
# 1분마다 보고하므로, 이 시간 동안 소식이 없으면 프로세스가 죽은 것이다.
RUNNING_TIMEOUT = timedelta(minutes=15)

ACTIVE = ("queued", "sent", "running")
IN_FLIGHT = ("sent", "running")
# 동시 실행 판정을 워커 사이에서 직렬화하는 advisory lock 키 (임의의 고정값)
_DISPATCH_LOCK = 0x50_17_7A_CE

SKIP_DISABLED = "비활성 장비"
SKIP_PENDING = "승인되지 않은 장비"
SKIP_OUTDATED = f"데몬 {MIN_DAEMON_VERSION} 이상 필요 — 업데이트 후 다시 요청하세요"
SKIP_BUSY = "이미 진행 중인 재전송이 있음"


class BackfillRequestError(ValueError):
    """요청 값이 규칙에 맞지 않음 — 메시지를 그대로 화면에 보여준다."""


# 파일 이름 패턴 — 장비의 **설정된 로그 폴더 안** 파일 이름만 받는다(웹에서 장비의 임의 경로를
# 읽게 하지 않는다). 데몬(soltrace_daemon.BACKFILL_PATTERN_RE)도 같은 규칙으로 한 번 더 막는다.
PATTERN_RE = re.compile(r"^[A-Za-z0-9._*?\[\]-]{1,100}$")


def clean_pattern(raw: Optional[str], label: str) -> Optional[str]:
    """빈 값은 None(그 로그는 읽지 않음). 규칙에 어긋나면 BackfillRequestError."""
    value = (raw or "").strip()
    if not value:
        return None
    if ".." in value or not PATTERN_RE.match(value):
        raise BackfillRequestError(
            f"{label} 파일 패턴은 로그 폴더 안의 파일 이름만 쓸 수 있습니다 "
            "(영문·숫자·. _ - * ? [ ], '/'·'..' 불가): " + value[:100]
        )
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _months_ago(d: date, months: int) -> date:
    """d 가 속한 달에서 months 개월 전 달의 1일."""
    idx = d.year * 12 + (d.month - 1) - months
    return date(idx // 12, idx % 12 + 1, 1)


def validate_range(date_from: date, date_to: date, today: date, retention_months: int) -> None:
    """기간 규칙. 보존 기간 밖은 파티션이 없어 default 파티션으로 가거나 곧 지워진다."""
    if date_from > date_to:
        raise BackfillRequestError("시작일이 종료일보다 늦습니다")
    if date_to > today:
        raise BackfillRequestError("종료일이 오늘보다 늦습니다")
    # 기간 길이에는 상한을 두지 않는다(2026-11-17 운영 결정) — 동시 2대 제한과 '기간 이전 파일은
    # 열지 않음'(bulk)이 부하를 막는다. 보존 기간 밖만 막는다: 파티션이 없어 default 로 가거나 곧 지워진다.
    earliest = _months_ago(today, retention_months)
    if date_from < earliest:
        raise BackfillRequestError(
            f"보존 기간({retention_months}개월) 밖입니다 — {earliest.isoformat()} 이후로 지정하세요"
        )


def skip_reason(device: Device, busy: bool) -> Optional[str]:
    """이 장비를 이번 요청에서 건너뛸 이유. 대상이면 None."""
    if device.status == "disabled":
        return SKIP_DISABLED
    if device.status != "confirmed":
        return SKIP_PENDING
    if not version_at_least(device.daemon_version, MIN_DAEMON_VERSION):
        return SKIP_OUTDATED
    if busy:
        return SKIP_BUSY
    return None


def request_for_group(db: Session, group_id: int, date_from: date, date_to: date,
                      requested_by: str, transfer_pattern: Optional[str] = "TransferLog*",
                      extended_pattern: Optional[str] = "ExtendedAllLog*") -> dict:
    """그룹 소속 장비마다 작업을 만든다. {'queued': n, 'skipped': n}."""
    today = datetime.now().date()   # 화면이 고르는 날짜와 같은 기준(서버 로컬 시간대)
    validate_range(date_from, date_to, today, retention.get_retention_months(db))
    transfer_pattern = clean_pattern(transfer_pattern, "TransferLog")
    extended_pattern = clean_pattern(extended_pattern, "ExtendedAllLog")
    if not transfer_pattern and not extended_pattern:
        raise BackfillRequestError("TransferLog·ExtendedAllLog 중 하나 이상을 선택하세요")

    devices = (
        db.query(Device)
        .join(DeviceGroup, DeviceGroup.device_id == Device.id)
        .filter(DeviceGroup.group_id == group_id)
        .order_by(Device.hostname)
        .all()
    )
    busy_ids = {
        row[0] for row in db.query(BackfillJob.device_id)
        .filter(BackfillJob.status.in_(ACTIVE),
                BackfillJob.device_id.in_([d.id for d in devices] or [0]))
    }
    now = _now()
    queued = skipped = 0
    for d in devices:
        reason = skip_reason(d, d.id in busy_ids)
        if reason == SKIP_BUSY:
            # 행을 만들지 않는다 — 만들면 '장비별 최근 작업' 목록에서 진행 중인 작업을 가린다
            skipped += 1
            continue
        job = BackfillJob(
            group_id=group_id, device_id=d.id, date_from=date_from, date_to=date_to,
            transfer_pattern=transfer_pattern, extended_pattern=extended_pattern,
            requested_by=requested_by, created_at=now, updated_at=now,
        )
        if reason:
            job.status = "skipped"
            job.message = reason
            job.finished_at = now
            skipped += 1
        else:
            queued += 1
        db.add(job)
    db.commit()
    log.info("backfill 요청 group=%s %s~%s queued=%d skipped=%d by=%s",
             group_id, date_from, date_to, queued, skipped, requested_by)
    return {"queued": queued, "skipped": skipped}


def expire_stale(db: Session) -> int:
    """응답 없는 작업을 실패로 닫아 동시 실행 슬롯을 비운다. 닫은 건수."""
    now = _now()
    n = 0
    for st, limit, msg in (
        ("sent", SENT_TIMEOUT, "장비가 지시를 받고도 시작하지 않음(데몬 재시작 등)"),
        ("running", RUNNING_TIMEOUT, "진행 보고가 끊김(데몬 재시작 또는 프로세스 종료)"),
    ):
        n += (
            db.query(BackfillJob)
            .filter(BackfillJob.status == st, BackfillJob.updated_at < now - limit)
            .update({BackfillJob.status: "failed", BackfillJob.message: msg,
                     BackfillJob.finished_at: now, BackfillJob.updated_at: now},
                    synchronize_session=False)
        )
    if n:
        log.warning("backfill 응답 없는 작업 %d건 실패 처리", n)
    return n


def dispatch(db: Session, device_id: int) -> Optional[dict]:
    """하트비트에서 부른다. 이 장비에 내려보낼 작업이 있고 슬롯이 비었으면 지시를 돌려준다.

    대기 작업이 없는 장비(거의 전부)는 인덱스 한 번 보고 끝난다. 커밋은 호출자(heartbeat)가 한다.
    """
    job = (
        db.query(BackfillJob)
        .filter(BackfillJob.device_id == device_id, BackfillJob.status == "queued")
        .order_by(BackfillJob.id)
        .first()
    )
    if job is None:
        return None
    # 워커 여럿이 동시에 '슬롯 남음'을 보고 3대 이상 내보내지 않게 트랜잭션 단위로 직렬화
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _DISPATCH_LOCK})
    expire_stale(db)
    in_flight = db.query(func.count(BackfillJob.id)).filter(BackfillJob.status.in_(IN_FLIGHT)).scalar()
    if in_flight >= MAX_RUNNING:
        return None
    now = _now()
    job.status = "sent"
    job.started_at = now
    job.updated_at = now
    return {"job_id": job.id, "date_from": job.date_from.isoformat(), "date_to": job.date_to.isoformat(),
            "transfer_pattern": job.transfer_pattern, "extended_pattern": job.extended_pattern}


class JobNotActive(Exception):
    """보고한 작업이 이 장비 것이 아니거나 이미 끝남 — bulk 는 멈춘다."""


def report_progress(db: Session, device_id: int, job_id: int, state: str,
                    sent: int, message: Optional[str]) -> None:
    job = db.query(BackfillJob).filter(BackfillJob.id == job_id,
                                       BackfillJob.device_id == device_id).first()
    if job is None or job.status not in IN_FLIGHT:
        raise JobNotActive()
    now = _now()
    job.status = state
    job.sent_count = max(0, sent)
    job.message = (message or None) and message[:500]
    job.updated_at = now
    if state in ("done", "failed"):
        job.finished_at = now
    db.commit()


def latest_for_group(db: Session, group_id: int) -> List[BackfillJob]:
    """그룹의 장비별 가장 최근 작업 (화면 진행 상황용)."""
    expire_stale(db)
    db.commit()
    latest_ids = (
        db.query(func.max(BackfillJob.id))
        .filter(BackfillJob.group_id == group_id)
        .group_by(BackfillJob.device_id)
    )
    return (
        db.query(BackfillJob)
        .filter(BackfillJob.id.in_(latest_ids))
        .order_by(BackfillJob.id.desc())
        .all()
    )
