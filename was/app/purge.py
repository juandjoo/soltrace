"""업로드 자동 purge — 텔코 CDN API 호출.

흐름:
  1. /ingest/logs 로 들어온 업로드 성공 로그 중, 그 장비가 속한 그룹의 purge_targets 에
     등록된 FTP 계정 것만 purge_jobs 에 쌓는다(enqueue_uploads). 그룹 스위치(groups.purge_active)와
     ID 스위치(purge_targets.enabled)가 둘 다 켜져 있어야 한다. 끈 동안 올라온 파일은 나중에 켜도 지우지 않는다.
  2. 워커 스레드가 쌓인 작업을 업로드 ID(=계정·서비스·볼륨)별로 묶어 filelist 한 번에 보낸다.
  3. 주소는 그룹 통신사(telcos.api_*_url), 계정은 업로드 ID(purge_targets.api_username/password)다.
     토큰은 그 계정으로 발급해 purge_targets.api_token 에 두고, 401 이면 다시 받아 한 번 더 보낸다.
  4. 실패하면 간격을 늘려 재시도하고, MAX_ATTEMPTS 를 넘기면 failed 로 남겨 화면에서 본다.

Gunicorn 워커마다 스레드가 하나씩 돌지만 `FOR UPDATE SKIP LOCKED` 로 집어 같은 작업을
두 번 보내지 않는다. 보내는 동안 행을 잡고 있다가 함께 커밋하므로, 중간에 죽으면 롤백돼
pending 으로 돌아간다.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import DeviceGroup, Group, PurgeJob, PurgeTarget, Telco

log = logging.getLogger("soltrace.purge")

HTTP_TIMEOUT = 15
TICK_SEC = 3                    # 큐 확인 주기
PICK_LIMIT = 500                # 한 번에 집는 작업 수
FILELIST_MAX = 100              # API 한 번에 보내는 경로 수
MAX_ATTEMPTS = 8                # 넘기면 failed — 1·2·4·…·60분 간격으로 약 3시간 시도
BACKOFF_BASE_SEC = 60
BACKOFF_MAX_SEC = 3600
TOKEN_EXPIRES_IN = "1y"         # 발급 요청 값 (샘플 그대로)
TOKEN_MAX_AGE = timedelta(days=300)   # 1년 만료 전에 미리 다시 받는다
KEEP_HISTORY_DAYS = 90
CLEANUP_EVERY_SEC = 3600
ERROR_SNIPPET = 300             # 오류 메시지에 남길 응답 본문 길이

STATUS_PENDING, STATUS_DONE, STATUS_FAILED = "pending", "done", "failed"


class PurgeApiError(Exception):
    """텔코 API 호출 실패. status 는 HTTP 코드(연결 실패면 None)."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


# ── 경로 ──────────────────────────────────────────────────────────────────────

def purge_path(file_path: str, strip_prefix: Optional[str]) -> Optional[str]:
    """업로드 경로 → API filelist 경로. strip_prefix 와 폴더 단위로 맞지 않으면 None.

    '/mnt1/800/cjenm/CLIP/a.mp4' 에서 '/mnt1/800' 을 떼면 '/cjenm/CLIP/a.mp4'.
    '/mnt1/8000' 처럼 폴더 중간에서 끊기는 접두어는 맞지 않는 것으로 본다.
    """
    prefix = (strip_prefix or "").strip().rstrip("/")
    if not prefix:
        return file_path if file_path.startswith("/") else "/" + file_path
    if not file_path.startswith(prefix + "/"):
        return None
    return file_path[len(prefix):]


# ── 큐 적재 (ingest 경로) ─────────────────────────────────────────────────────

def enqueue_uploads(db: Session, device_id: int, logs: list) -> int:
    """업로드 성공 로그 중 purge 대상만 purge_jobs 에 넣는다. 넣은 건수를 돌려준다."""
    uploads = [e for e in logs
               if e.action == "upload" and e.status == "success" and e.username and e.file_path]
    if not uploads:
        return 0
    names = {e.username for e in uploads}
    targets = (
        db.query(PurgeTarget)
        .join(DeviceGroup, DeviceGroup.group_id == PurgeTarget.group_id)
        .join(Group, Group.id == PurgeTarget.group_id)
        .filter(DeviceGroup.device_id == device_id, PurgeTarget.ftp_username.in_(names),
                PurgeTarget.enabled.is_(True), Group.purge_active.is_(True))
        .all()
    )
    if not targets:
        return 0
    by_user: Dict[str, List[PurgeTarget]] = {}
    for t in targets:
        by_user.setdefault(t.ftp_username, []).append(t)

    now = datetime.now(timezone.utc)
    jobs = []
    for e in uploads:
        for t in by_user.get(e.username, []):
            path = purge_path(e.file_path, t.strip_prefix)
            job = PurgeJob(
                group_id=t.group_id, target_id=t.id, device_id=device_id, ftp_username=e.username,
                file_path=e.file_path, purge_path=path, service=t.service, volume=t.volume,
                created_at=now, next_try_at=now,
            )
            if path is None:
                # 설정이 틀린 것이라 재시도해도 같다 — 바로 실패로 남겨 화면에서 보이게
                job.status = STATUS_FAILED
                job.last_error = f"경로가 마운트 경로({t.strip_prefix})로 시작하지 않음"
                job.done_at = now
            jobs.append(job)
    db.add_all(jobs)
    db.commit()
    return len(jobs)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _check_url(url: Optional[str], label: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise PurgeApiError(f"{label} 이 http(s) 주소가 아닙니다")
    return url


def _http_post(url: str, payload: dict, headers: dict) -> Tuple[int, dict, str]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, dict(e.headers or {}), body
    except (urllib.error.URLError, OSError) as e:
        raise PurgeApiError(f"연결 실패: {getattr(e, 'reason', e)}")


_TOKEN_HEADERS = ("x-subject-token", "x-auth-token", "authorization")
_TOKEN_KEYS = ("access_token", "accessToken", "token", "auth_token", "authToken", "id")
_TOKEN_WRAPPERS = ("data", "result", "response")


def _find_token(obj, depth: int = 0) -> Optional[str]:
    """응답 본문에서 토큰 값을 찾는다 — 형식이 텔코마다 달라 흔한 모양을 차례로 본다.

    {"token": "..."} / {"access_token": "..."} / {"token": {"id": "..."}} /
    {"data": {"token": "..."}} 등. 'id' 는 token 객체 안에서만 토큰으로 본다.
    """
    if depth > 3 or not isinstance(obj, dict):
        return None
    for key in _TOKEN_KEYS:
        if key == "id" and depth == 0:
            continue
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            found = _find_token(val, depth + 1)
            if found:
                return found
    for key in _TOKEN_WRAPPERS:
        found = _find_token(obj.get(key), depth + 1)
        if found:
            return found
    return None


def extract_token(headers: dict, body: str) -> Tuple[Optional[str], str]:
    """(토큰, 어디서 찾았나). 못 찾으면 (None, '')."""
    lower = {k.lower(): v for k, v in headers.items()}
    for h in _TOKEN_HEADERS:
        val = (lower.get(h) or "").strip()
        if val:
            return (val[7:].strip() if val.lower().startswith("bearer ") else val), f"헤더 {h}"
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    if isinstance(data, str) and data.strip():
        return data.strip(), "응답 본문(문자열)"
    if data is not None:
        token = _find_token(data)
        return (token, "응답 본문") if token else (None, "")
    plain = body.strip().strip('"')
    if plain and " " not in plain and len(plain) < 4096:
        return plain, "응답 본문(텍스트)"
    return None, ""


def request_token(auth_url: Optional[str], username: Optional[str], password: Optional[str]) -> str:
    """토큰을 발급받아 돌려준다(저장하지 않는다). 그룹 모달의 '토큰 발급 테스트'도 이것을 쓴다."""
    url = _check_url(auth_url, "토큰 발급 URL")
    if not username or not password:
        raise PurgeApiError("업로드 ID 에 API 계정·비밀번호가 없습니다")
    status, headers, body = _http_post(url, {
        "username": username,
        "password": password,
        "expiresIn": TOKEN_EXPIRES_IN,
    }, {})
    if not 200 <= status < 300:
        raise PurgeApiError(f"토큰 발급 HTTP {status}: {body[:ERROR_SNIPPET]}", status)
    token, _ = extract_token(headers, body)
    if not token:
        raise PurgeApiError(f"응답에서 토큰을 찾지 못함: {body[:ERROR_SNIPPET]}")
    return token


def issue_token(db: Session, telco: Telco, target: PurgeTarget) -> str:
    """업로드 ID 계정으로 토큰을 새로 받아 그 ID 에 저장한다."""
    token = request_token(telco.api_auth_url, target.api_username, target.api_password)
    target.api_token = token
    target.api_token_at = datetime.now(timezone.utc)
    db.flush()
    return token


def _usable_token(db: Session, telco: Telco, target: PurgeTarget) -> str:
    fresh = target.api_token_at and datetime.now(timezone.utc) - target.api_token_at < TOKEN_MAX_AGE
    if target.api_token and fresh:
        return target.api_token
    return issue_token(db, telco, target)


def build_purge_url(template: str, service: str, volume: str) -> str:
    url = _check_url(template, "purge URL")
    if "{service}" not in url or "{volume}" not in url:
        raise PurgeApiError("purge URL 에 {service} 와 {volume} 자리표시자가 있어야 합니다")
    return url.replace("{service}", quote(service, safe="")).replace("{volume}", quote(volume, safe=""))


def send_purge(db: Session, telco: Telco, target: PurgeTarget, service: str, volume: str,
               paths: List[str]) -> None:
    """filelist 한 번 호출. 401 이면 토큰을 다시 받아 한 번 더. 실패면 PurgeApiError."""
    url = build_purge_url(telco.api_purge_url, service, volume)
    payload = {"filelist": paths}
    token = _usable_token(db, telco, target)
    status, _, body = _http_post(url, payload, {"X-Auth-Token": token})
    if status == 401:
        token = issue_token(db, telco, target)
        status, _, body = _http_post(url, payload, {"X-Auth-Token": token})
    if not 200 <= status < 300:
        raise PurgeApiError(f"purge HTTP {status}: {body[:ERROR_SNIPPET]}", status)


# ── 워커 ──────────────────────────────────────────────────────────────────────

def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(BACKOFF_BASE_SEC * 2 ** max(attempts - 1, 0), BACKOFF_MAX_SEC))


def _mark_failed_attempt(jobs: List[PurgeJob], error: str, now: datetime) -> None:
    for j in jobs:
        j.attempts += 1
        j.last_error = error[:2000]
        if j.attempts >= MAX_ATTEMPTS:
            j.status = STATUS_FAILED
            j.done_at = now
        else:
            j.next_try_at = now + _backoff(j.attempts)


def _targets_with_telco(db: Session, target_ids: set) -> Dict[int, Tuple[PurgeTarget, Optional[Telco]]]:
    rows = (
        db.query(PurgeTarget, Telco)
        .join(Group, Group.id == PurgeTarget.group_id)
        .outerjoin(Telco, Telco.name == Group.telco)
        .filter(PurgeTarget.id.in_(target_ids))
        .all()
    )
    return {t.id: (t, telco) for t, telco in rows}


def process_due(db: Session) -> int:
    """기한이 된 pending 작업을 묶어 보낸다. 처리한 작업 수를 돌려준다."""
    now = datetime.now(timezone.utc)
    jobs = (
        db.query(PurgeJob)
        .filter(PurgeJob.status == STATUS_PENDING, PurgeJob.next_try_at <= now)
        .order_by(PurgeJob.id)
        .limit(PICK_LIMIT)
        .with_for_update(skip_locked=True)
        .all()
    )
    if not jobs:
        return 0
    targets = _targets_with_telco(db, {j.target_id for j in jobs if j.target_id})
    buckets: Dict[tuple, List[PurgeJob]] = {}
    for j in jobs:
        buckets.setdefault((j.target_id, j.service, j.volume), []).append(j)

    for (target_id, service, volume), bucket in buckets.items():
        target, telco = targets.get(target_id, (None, None))
        if target is None:
            _mark_failed_attempt(bucket, "업로드 ID 설정이 삭제됨", now)
            continue
        if telco is None or not telco.api_purge_url:
            _mark_failed_attempt(bucket, "그룹의 통신사에 purge API 주소가 설정되지 않음", now)
            continue
        for i in range(0, len(bucket), FILELIST_MAX):
            chunk = bucket[i:i + FILELIST_MAX]
            paths = list(dict.fromkeys(j.purge_path for j in chunk))
            try:
                send_purge(db, telco, target, service, volume, paths)
            except PurgeApiError as e:
                log.warning("purge 실패 [%s %s/%s] %d건: %s", telco.name, service, volume, len(chunk), e)
                _mark_failed_attempt(chunk, str(e), now)
                continue
            done_at = datetime.now(timezone.utc)
            for j in chunk:
                j.status, j.done_at, j.last_error = STATUS_DONE, done_at, None
                j.attempts += 1
            log.info("purge 완료 [%s %s/%s] %d건", telco.name, service, volume, len(paths))
    db.commit()
    return len(jobs)


def cleanup_history(db: Session) -> None:
    db.execute(text(
        "DELETE FROM purge_jobs WHERE status <> 'pending' "
        "AND created_at < NOW() - make_interval(days => :days)"
    ), {"days": KEEP_HISTORY_DAYS})
    db.commit()


class PurgeWorker:
    def __init__(self, session_factory):
        self._sf = session_factory
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_cleanup = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="purge-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=HTTP_TIMEOUT * 2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            busy = False
            try:
                with self._sf() as db:
                    busy = process_due(db) >= PICK_LIMIT
                    if time.monotonic() - self._last_cleanup > CLEANUP_EVERY_SEC:
                        cleanup_history(db)
                        self._last_cleanup = time.monotonic()
            except Exception:
                log.exception("purge 워커 오류")
            if not busy:
                self._stop.wait(TICK_SEC)


_worker: Optional[PurgeWorker] = None


def init_worker(session_factory) -> None:
    global _worker
    _worker = PurgeWorker(session_factory)
    _worker.start()


def shutdown_worker() -> None:
    if _worker:
        _worker.stop()
