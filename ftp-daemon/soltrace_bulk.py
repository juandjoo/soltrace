#!/usr/bin/env python3
"""
SolTrace Bulk Importer
proftpd 로그 파일의 과거 데이터를 WAS에 일괄 전송한다.

사용법:
  python3 soltrace_bulk.py [옵션]

옵션:
  --transfer-log PATH  TransferLog 파일 경로 (기본: config.ini 값)
  --extended-log PATH  ExtendedAllLog 파일 경로 (기본: config.ini 값)
  --date-from YYYY-MM-DD  이 날짜 이후 데이터만 전송 (선택)
  --date-to   YYYY-MM-DD  이 날짜 이전 데이터만 전송 (선택)
  --batch-size N       한 번에 전송할 건수 (기본: 500)
  --dry-run            실제 전송 없이 파싱 결과만 확인
  --no-extended        ExtendedAllLog 무시

예시:
  # 2026-06-01 이후 전체 전송
  python3 soltrace_bulk.py --date-from 2026-06-01

  # TransferLog만 특정 기간
  python3 soltrace_bulk.py --no-extended --date-from 2026-05-01 --date-to 2026-05-31

  # 파싱 테스트 (전송 안 함)
  python3 soltrace_bulk.py --dry-run --date-from 2026-06-01
"""
import argparse
import configparser
import gzip
import hashlib
import json
import logging
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# Import parsers from daemon module
sys.path.insert(0, str(Path(__file__).parent))
from soltrace_daemon import (
    parse_extended_log,
    parse_transfer_log,
    load_config,
    get_device_key,
    setup_logging,
)

log = logging.getLogger("soltrace.bulk")


def parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def iter_file(path: str, parser, date_from: Optional[datetime], date_to: Optional[datetime]):
    """파일 전체를 순회하며 필터 조건에 맞는 항목 yield. .gz 파일 자동 처리."""
    p = Path(path)
    if not p.exists():
        log.warning("File not found: %s", path)
        return
    total = 0
    skipped_date = 0
    parse_errors = 0
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            entry = parser(line)
            if entry is None:
                parse_errors += 1
                continue
            total += 1
            try:
                dt = datetime.fromisoformat(entry["log_time"])
            except Exception:
                yield entry
                continue
            if date_from and dt < date_from:
                skipped_date += 1
                continue
            if date_to and dt > date_to:
                skipped_date += 1
                continue
            yield entry
    log.info("File %s: parsed=%d skipped_date=%d parse_errors=%d",
             path, total, skipped_date, parse_errors)


def send_batch(was_url: str, device_key: str, batch: list, session: requests.Session) -> bool:
    url = f"{was_url.rstrip('/')}/api/v1/ingest/logs"
    try:
        r = session.post(url, json={"device_key": device_key, "logs": batch}, timeout=30)
        r.raise_for_status()
        data = r.json()
        log.debug("Batch sent: accepted=%d rejected=%d", data.get("accepted", 0), data.get("rejected", 0))
        return True
    except requests.exceptions.RequestException as e:
        log.error("Send failed: %s", e)
        return False


def run_bulk(args):
    cfg = load_config()
    dcfg = cfg["daemon"]
    setup_logging(cfg)

    was_url = dcfg["was_url"]
    device_key = get_device_key()
    transfer_log = args.transfer_log or dcfg["transfer_log"]
    extended_log = args.extended_log or dcfg["extended_log"]
    batch_size = args.batch_size
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)

    log.info("=== SolTrace Bulk Import ===")
    log.info("WAS: %s", was_url)
    log.info("Device key: %s...", device_key[:8])
    log.info("Period: %s ~ %s", args.date_from or "all", args.date_to or "now")
    if args.dry_run:
        log.info("DRY RUN mode - no data will be sent")

    # Register device first
    if not args.dry_run:
        hostname = socket.gethostname()
        sess = requests.Session()
        sess.headers["Content-Type"] = "application/json"
        try:
            r = sess.post(
                f"{was_url.rstrip('/')}/api/v1/ingest/register",
                json={
                    "device_key": device_key,
                    "hostname": hostname,
                    "daemon_version": "1.0.0-bulk",
                },
                timeout=15,
            )
            r.raise_for_status()
            log.info("Device registered/updated: status=%s", r.json().get("status"))
        except Exception as e:
            log.error("Registration failed: %s. Continuing anyway...", e)
    else:
        sess = None

    total_sent = 0
    total_failed = 0
    batch = []

    def flush_batch():
        nonlocal total_sent, total_failed
        if not batch:
            return
        if args.dry_run:
            log.info("[DRY RUN] Would send %d entries", len(batch))
            total_sent += len(batch)
        else:
            ok = send_batch(was_url, device_key, list(batch), sess)
            if ok:
                total_sent += len(batch)
            else:
                total_failed += len(batch)
                log.error("Failed to send batch of %d, retrying once...", len(batch))
                time.sleep(5)
                ok2 = send_batch(was_url, device_key, list(batch), sess)
                if ok2:
                    total_sent += len(batch)
                    total_failed -= len(batch)
                else:
                    log.error("Retry failed. %d entries lost.", len(batch))
        batch.clear()

    # Process TransferLog
    log.info("Processing TransferLog: %s", transfer_log)
    for entry in iter_file(transfer_log, parse_transfer_log, date_from, date_to):
        batch.append(entry)
        if len(batch) >= batch_size:
            flush_batch()
            time.sleep(0.2)   # throttle

    flush_batch()

    # Process ExtendedAllLog
    if not args.no_extended and extended_log:
        log.info("Processing ExtendedAllLog: %s", extended_log)
        for entry in iter_file(extended_log, parse_extended_log, date_from, date_to):
            batch.append(entry)
            if len(batch) >= batch_size:
                flush_batch()
                time.sleep(0.2)

    flush_batch()

    log.info("=== Bulk import complete ===")
    log.info("Total sent: %d  Failed: %d", total_sent, total_failed)
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
    args = parser.parse_args()

    success = run_bulk(args)
    sys.exit(0 if success else 1)
