#!/usr/bin/env python3
"""
SolTrace Bulk Importer
proftpd 로그 파일의 과거 데이터를 WAS에 일괄 전송한다.

두 가지로 쓰인다.
  - 손으로 실행: 장비에 접속해 아래 옵션으로 돌린다.
  - 웹 재전송(--job-id): 그룹 관리 > 로그 재전송을 누르면 데몬이 하트비트 지시를 받아 이 스크립트를
    **별도 프로세스**로 띄운다. 파서가 세션(USER·RNFR) 상태를 모듈 전역에 두므로, 같은 프로세스에서
    돌리면 과거 로그의 세션이 실시간 수집과 섞인다. 진행 상황은 WAS 에 직접 보고한다.

이미 WAS 에 있는 행은 WAS 가 (device_id, log_time, row_hash) 로 걸러내므로 다시 보내도 중복되지 않는다.

사용법:
  python3 soltrace_bulk.py [옵션]

옵션:
  --transfer-log PATH  TransferLog 파일 경로 또는 glob 패턴 (기본: config.ini 값)
  --extended-log PATH  ExtendedAllLog 파일 경로 또는 glob 패턴 (기본: config.ini 값)
  --date-from YYYY-MM-DD  이 날짜부터 (포함)
  --date-to   YYYY-MM-DD  이 날짜까지 (포함 — 그날 23:59:59 까지)
  --batch-size N       한 번에 전송할 건수 (기본: 500)
  --dry-run            실제 전송 없이 파싱 결과만 확인
  --no-extended        ExtendedAllLog 무시
  --no-transfer        TransferLog 무시
  --rotated            config.ini 경로 뒤에 붙은 회전·압축 파일(경로*)까지 모두 읽는다
  --transfer-name PAT  config.ini 의 TransferLog 폴더 안에서 이 이름 패턴만 (예: TransferLog.*.gz)
  --extended-name PAT  config.ini 의 ExtendedAllLog 폴더 안에서 이 이름 패턴만 (예: ExtendedAllLog.*.gz)
  --job-id N           웹 재전송 작업 번호 (데몬이 붙인다 — --rotated 를 겸한다)

예시:
  # 압축 일별 로그만 복구 (glob 패턴)
  python3 soltrace_bulk.py --no-transfer \
      --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"

  # 특정 기간 압축 로그
  python3 soltrace_bulk.py --no-transfer --date-from 2026-05-01 --date-to 2026-05-31 \
      --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"

  # TransferLog만 특정 기간
  python3 soltrace_bulk.py --no-extended --date-from 2026-05-01 --date-to 2026-05-31

  # 파싱 테스트 (전송 안 함)
  python3 soltrace_bulk.py --dry-run --no-transfer \
      --extended-log "/usr/service/logs/proftpd/ExtendedAllLog.*.gz"
"""
import argparse
import glob as _glob
import gzip
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# Import parsers from daemon module
sys.path.insert(0, str(Path(__file__).parent))
from soltrace_daemon import (  # noqa: E402
    DAEMON_VERSION,
    parse_extended_log,
    parse_transfer_log,
    load_config,
    get_device_key,
    setup_logging,
)

log = logging.getLogger("soltrace.bulk")

# WAS 가 한 요청에 받는 최대 건수 (was/app/schemas.py LogBatch.logs max_length)
MAX_BATCH = 500
# WAS 가 바쁘면(503 쓰기 버퍼 포화, 502 재시작 중, 네트워크 오류) 기다렸다 같은 묶음을 다시 보낸다.
# 예전에는 한 번만 재시도하고 그 묶음을 버렸다 — 사람이 지켜보지 않는 웹 재전송에서는 조용히 빠진다.
BACKOFF_START = 30
BACKOFF_MAX = 300
GIVE_UP_AFTER = 30 * 60        # 한 묶음을 이만큼 못 보내면 포기
PROGRESS_EVERY = 60            # 진행 보고 주기(초) — WAS 는 15분 소식이 없으면 실패로 본다
THROTTLE = 0.2                 # 묶음 사이 쉬는 시간 — 실시간 수집과 WAS 를 덜 누르게


class JobStopped(Exception):
    """작업을 더 진행하지 않는다 — message 는 WAS 에 실패 사유로 남는다."""


def parse_date(s: Optional[str], end: bool = False) -> Optional[datetime]:
    """YYYY-MM-DD → 장비 로컬 자정(UTC). end=True 면 다음 날 자정(배타적 상한).

    예전에는 종료일도 그날 0시로 잡아 '포함'이라던 종료일 하루가 통째로 빠졌다.
    """
    if not s:
        return None
    d = datetime.strptime(s, "%Y-%m-%d")
    if end:
        d += timedelta(days=1)
    return d.astimezone(timezone.utc)


def _entry_time(entry: dict) -> Optional[datetime]:
    """파서가 만든 log_time('YYYY-MM-DDTHH:MM:SS+00:00', 항상 UTC).
    datetime.fromisoformat 은 3.7+ 라 3.6 장비에서 기간 필터가 통째로 무시됐다."""
    try:
        return datetime.strptime(entry["log_time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def iter_file(path: str, parser, date_from: Optional[datetime], date_to: Optional[datetime]):
    """파일 전체를 순회하며 [date_from, date_to) 에 드는 항목 yield. .gz 파일 자동 처리."""
    p = Path(path)
    if not p.exists():
        log.warning("File not found: %s", path)
        return
    # 마지막 수정이 시작 시각보다 이르면 그 안의 줄은 모두 기간 밖이다 — 몇 년 치 .gz 를 풀지 않는다
    if date_from and datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < date_from:
        log.info("--- 건너뜀(기간 이전 파일): %s", p.name)
        return
    total = 0
    skipped_date = 0
    parse_errors = 0
    fname = p.name
    opener = gzip.open if path.endswith(".gz") else open
    log.info(">>> 파일 읽기 시작: %s", fname)
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if lineno % 50000 == 0:
                log.info("  %s: %d줄 읽음 (파싱 %d건)", fname, lineno, total)
            entry = parser(line)
            if entry is None:
                parse_errors += 1
                continue
            total += 1
            dt = _entry_time(entry)
            if dt is not None and ((date_from and dt < date_from) or (date_to and dt >= date_to)):
                skipped_date += 1
                continue
            yield entry
    log.info("<<< 완료: %s — 파싱 %d건 / 날짜제외 %d건 / 파싱오류 %d건",
             fname, total, skipped_date, parse_errors)


def iter_files(pattern: str, parser, date_from: Optional[datetime], date_to: Optional[datetime]):
    """glob 패턴 또는 단일 파일 경로를 받아 이름순으로 처리."""
    matched = sorted(m for m in _glob.glob(pattern) if os.path.isfile(m))
    if not matched:
        # glob 매칭 없으면 그대로 단일 파일로 시도
        yield from iter_file(pattern, parser, date_from, date_to)
        return
    log.info("glob '%s' → %d 파일", pattern, len(matched))
    for path in matched:
        yield from iter_file(path, parser, date_from, date_to)


class Sender:
    """묶음 전송. WAS 가 바쁘면 기다렸다 다시 보내고, 장비가 막혔으면 멈춘다."""

    def __init__(self, was_url: str, device_key: str, session: requests.Session, backfill: bool):
        self.url = f"{was_url.rstrip('/')}/api/v1/ingest/logs"
        self.device_key = device_key
        self.session = session
        self.backfill = backfill

    def send(self, batch: list) -> Optional[int]:
        """받은 건수. GIVE_UP_AFTER 동안 못 보내면 None. 장비 비활성·삭제면 JobStopped."""
        body = {"device_key": self.device_key, "logs": batch}
        if self.backfill:
            body["backfill"] = True    # WAS 가 업로드 자동 purge 를 걸지 않는다
        deadline = time.monotonic() + GIVE_UP_AFTER
        wait = BACKOFF_START
        while True:
            try:
                r = self.session.post(self.url, json=body, timeout=30)
                if r.status_code == 403:
                    raise JobStopped("장비가 비활성화되어 WAS 가 로그를 받지 않음")
                if r.status_code in (401, 404):
                    raise JobStopped("WAS 에서 장비가 삭제됨")
                if 400 <= r.status_code < 500:
                    # 요청 자체가 틀린 것(422 등) — 기다려 다시 보내도 같다
                    raise JobStopped("WAS 가 로그 묶음을 거부함 (HTTP %d)" % r.status_code)
                r.raise_for_status()
                return int(r.json().get("accepted", 0))
            except requests.exceptions.RequestException as e:
                if time.monotonic() + wait > deadline:
                    log.error("Send failed, giving up after %ds: %s", GIVE_UP_AFTER, e)
                    return None
                log.warning("Send failed (%s) — %d초 후 다시 보냄", e, wait)
                time.sleep(wait)
                wait = min(wait * 2, BACKOFF_MAX)


class Reporter:
    """웹 재전송 진행 보고. job_id 가 없으면(손으로 실행) 아무것도 하지 않는다."""

    def __init__(self, was_url: str, device_key: str, session: requests.Session, job_id: Optional[int]):
        self.url = f"{was_url.rstrip('/')}/api/v1/ingest/backfill/progress"
        self.device_key = device_key
        self.session = session
        self.job_id = job_id
        self._last = 0.0

    def report(self, state: str, sent: int, message: Optional[str] = None, force: bool = False):
        if self.job_id is None:
            return
        now = time.monotonic()
        if not force and now - self._last < PROGRESS_EVERY:
            return
        self._last = now
        try:
            r = self.session.post(self.url, timeout=15, json={
                "device_key": self.device_key, "job_id": self.job_id,
                "state": state, "sent": sent, "message": message,
            })
        except requests.exceptions.RequestException as e:
            log.warning("Progress report failed: %s", e)   # 다음 주기에 다시 보낸다
            return
        if r.status_code == 410:
            raise JobStopped("WAS 에서 작업이 이미 종료됨")
        if r.status_code >= 400:
            log.warning("Progress report rejected: HTTP %d", r.status_code)


def _register(was_url: str, device_key: str, sess: requests.Session):
    try:
        r = sess.post(
            f"{was_url.rstrip('/')}/api/v1/ingest/register",
            json={
                "device_key": device_key,
                "hostname": socket.gethostname(),
                # 예전에는 '1.0.0-bulk' 를 보내 장비의 데몬 버전이 그 값으로 덮였다(데몬은 바뀔 때만
                # 다시 보고하므로 재시작 전까지 구버전 ⚠︎ 로 굳었다). 실제 설치된 버전을 보낸다.
                "daemon_version": DAEMON_VERSION,
            },
            timeout=15,
        )
        r.raise_for_status()
        log.info("Device registered/updated: status=%s", r.json().get("status"))
    except Exception as e:
        log.error("Registration failed: %s. Continuing anyway...", e)


def run_bulk(args) -> bool:
    cfg = load_config()
    dcfg = cfg["daemon"]
    job_id = args.job_id
    if job_id is not None:
        # 데몬과 같은 로그 파일을 두 프로세스가 회전시키면 서로 덮는다 — 옆 파일에 쓴다
        dcfg["log_file"] = str(Path(dcfg["log_file"]).with_name("backfill.log"))
    setup_logging(cfg)

    was_url = dcfg["was_url"]
    device_key = get_device_key()
    rotated = args.rotated or job_id is not None
    transfer_log = args.transfer_log or dcfg["transfer_log"]
    extended_log = args.extended_log or dcfg["extended_log"]
    if rotated:
        # 회전·압축본(TransferLog.1, ExtendedAllLog.1.gz 등)까지 — 지금 쓰는 파일도 포함.
        # 파일 이름에 날짜가 없어도 된다: 기간은 각 줄의 시각으로 거른다.
        transfer_log = transfer_log and transfer_log + "*"
        extended_log = extended_log and extended_log + "*"
    # 웹에서 고른 이름 패턴 — 폴더는 config.ini 의 것을 쓰고 이름만 바꾼다
    if args.transfer_name:
        transfer_log = os.path.join(os.path.dirname(dcfg["transfer_log"]), args.transfer_name)
    if args.extended_name:
        extended_log = os.path.join(os.path.dirname(dcfg["extended_log"]), args.extended_name)
    # WAS(schemas.LogBatch)가 한 요청에 받는 상한. 넘기면 422 로 전량이 버려지므로 여기서 자른다.
    batch_size = max(1, min(args.batch_size, MAX_BATCH))
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to, end=True)

    log.info("=== SolTrace Bulk Import%s ===", " (job %d)" % job_id if job_id is not None else "")
    log.info("WAS: %s", was_url)
    log.info("Device key: %s...", device_key[:8])
    log.info("Period: %s ~ %s", args.date_from or "all", args.date_to or "now")
    if args.dry_run:
        log.info("DRY RUN mode - no data will be sent")

    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"
    sess.verify = dcfg.get("ssl_verify", "true").lower() not in ("false", "0", "no")
    # 웹 재전송은 데몬이 이미 등록된 장비에서 띄우므로 등록하지 않는다
    if not args.dry_run and job_id is None:
        _register(was_url, device_key, sess)
    sender = Sender(was_url, device_key, sess, backfill=job_id is not None)
    reporter = Reporter(was_url, device_key, sess, job_id)

    total_sent = 0
    total_failed = 0
    batch = []

    def flush_batch():
        nonlocal total_sent, total_failed
        if not batch:
            return
        if args.dry_run:
            total_sent += len(batch)
            log.info("[DRY RUN] %d건 (누적 %d건)", len(batch), total_sent)
        else:
            accepted = sender.send(list(batch))
            if accepted is None:
                total_failed += len(batch)
                if job_id is not None:
                    raise JobStopped("WAS 에 %d분 동안 보내지 못해 중단" % (GIVE_UP_AFTER // 60))
                log.error("%d entries lost.", len(batch))
            else:
                total_sent += accepted
                log.info("  누적 전송: %d건", total_sent)
            reporter.report("running", total_sent)
        batch.clear()

    def process(pattern, parser):
        for entry in iter_files(pattern, parser, date_from, date_to):
            batch.append(entry)
            if len(batch) >= batch_size:
                flush_batch()
                time.sleep(THROTTLE)
        flush_batch()

    try:
        reporter.report("running", 0, force=True)
        if not args.no_transfer and transfer_log:
            log.info("Processing TransferLog: %s", transfer_log)
            process(transfer_log, parse_transfer_log)
        if not args.no_extended and extended_log:
            log.info("Processing ExtendedAllLog: %s", extended_log)
            process(extended_log, parse_extended_log)
    except JobStopped as e:
        log.error("Bulk import stopped: %s", e)
        try:
            reporter.report("failed", total_sent, str(e), force=True)
        except JobStopped:
            pass
        return False
    except Exception as e:      # 파일 읽기 오류 등 — 작업을 '진행 중'으로 남겨 두지 않는다
        log.exception("Bulk import crashed")
        try:
            reporter.report("failed", total_sent, "장비에서 오류: %s" % e, force=True)
        except JobStopped:
            pass
        return False

    log.info("=== Bulk import complete ===")
    log.info("Total sent: %d  Failed: %d", total_sent, total_failed)
    try:
        reporter.report("done", total_sent, force=True)
    except JobStopped:
        pass
    return total_failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SolTrace Bulk Log Importer")
    parser.add_argument("--transfer-log", help="TransferLog 파일 경로")
    parser.add_argument("--extended-log", help="ExtendedAllLog 파일 경로")
    parser.add_argument("--date-from", metavar="YYYY-MM-DD", help="시작 날짜 (포함)")
    parser.add_argument("--date-to",   metavar="YYYY-MM-DD", help="종료 날짜 (포함)")
    parser.add_argument("--batch-size", type=int, default=500, help="배치 크기 (기본: 500)")
    parser.add_argument("--dry-run", action="store_true", help="전송 없이 테스트")
    parser.add_argument("--no-extended", action="store_true", help="ExtendedAllLog 무시")
    parser.add_argument("--no-transfer", action="store_true", help="TransferLog 무시")
    parser.add_argument("--rotated", action="store_true", help="회전·압축 파일(경로*)까지 읽기")
    parser.add_argument("--transfer-name", help="TransferLog 폴더 안 파일 이름 패턴")
    parser.add_argument("--extended-name", help="ExtendedAllLog 폴더 안 파일 이름 패턴")
    parser.add_argument("--job-id", type=int, help="웹 재전송 작업 번호 (데몬이 붙인다)")
    args = parser.parse_args()

    success = run_bulk(args)
    sys.exit(0 if success else 1)
