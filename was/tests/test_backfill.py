"""로그 재전송(backfill) — 요청 규칙과 장비 쪽 파일 선택·기간 필터."""
import gzip
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.backfill import (
    MAX_DAYS, SKIP_BUSY, SKIP_DISABLED, SKIP_OUTDATED, SKIP_PENDING,
    BackfillRequestError, _months_ago, clean_pattern, skip_reason, validate_range,
)
from app.gitinfo import version_at_least

DAEMON_DIR = Path(__file__).resolve().parents[2] / "ftp-daemon"
TODAY = date(2026, 11, 17)


# ── 기간 ─────────────────────────────────────────────────────────────────────

def test_range_ok_31_days():
    validate_range(date(2026, 10, 18), TODAY, TODAY, 36)   # 시작·끝 포함 31일


@pytest.mark.parametrize("f,t,msg", [
    (date(2026, 10, 17), TODAY, f"최대 {MAX_DAYS}일"),
    (date(2026, 11, 10), date(2026, 11, 9), "시작일이"),
    (date(2026, 11, 10), date(2026, 11, 18), "오늘보다"),
    (date(2026, 9, 30), date(2026, 10, 2), "보존 기간"),   # 보존 1개월 → 2026-10-01 부터
])
def test_range_rejected(f, t, msg):
    with pytest.raises(BackfillRequestError, match=msg):
        validate_range(f, t, TODAY, 1)


def test_months_ago_crosses_year():
    assert _months_ago(date(2026, 2, 15), 3) == date(2025, 11, 1)


# ── 파일 패턴 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["ExtendedAllLog.*.gz", "TransferLog*", "xferlog-2026[01]?.gz"])
def test_pattern_ok(raw):
    assert clean_pattern(" " + raw + " ", "x") == raw


@pytest.mark.parametrize("raw", ["../etc/passwd", "/etc/shadow", "a/b*", "a b", "log;rm", "..*"])
def test_pattern_rejected(raw):
    with pytest.raises(BackfillRequestError):
        clean_pattern(raw, "x")


def test_pattern_empty_means_skip():
    assert clean_pattern("  ", "x") is None
    assert clean_pattern(None, "x") is None


# ── 대상 장비 ────────────────────────────────────────────────────────────────

def _dev(status="confirmed", ver="1.3.0"):
    return SimpleNamespace(status=status, daemon_version=ver)


@pytest.mark.parametrize("dev,busy,reason", [
    (_dev(), False, None),
    (_dev(ver="1.4.2"), False, None),
    (_dev(status="disabled"), False, SKIP_DISABLED),
    (_dev(status="pending"), False, SKIP_PENDING),
    (_dev(ver="1.2.1"), False, SKIP_OUTDATED),
    (_dev(ver=None), False, SKIP_OUTDATED),       # 모르는 버전은 지원 안 함으로
    (_dev(), True, SKIP_BUSY),
])
def test_skip_reason(dev, busy, reason):
    assert skip_reason(dev, busy) == reason


def test_version_at_least():
    assert version_at_least("1.3.0", "1.3.0")
    assert version_at_least("1.10.0", "1.3.0")      # 문자열 비교가 아니다
    assert not version_at_least("1.2.9", "1.3.0")
    assert not version_at_least("garbage", "1.3.0")


# ── 장비 쪽(ftp-daemon) — 패턴 인자, 날짜 없는 회전 파일, 종료일 포함 ──────────

@pytest.fixture()
def daemon_mods(monkeypatch):
    pytest.importorskip("psutil")
    monkeypatch.syspath_prepend(str(DAEMON_DIR))
    for m in ("soltrace_bulk", "soltrace_daemon"):
        sys.modules.pop(m, None)
    import soltrace_bulk
    import soltrace_daemon
    return soltrace_daemon, soltrace_bulk


def test_daemon_file_args(daemon_mods):
    d, _ = daemon_mods
    args = d.SolTraceDaemon._backfill_file_args
    assert args({"transfer_pattern": None, "extended_pattern": "ExtendedAllLog.*.gz"}) == \
        ["--no-transfer", "--extended-name", "ExtendedAllLog.*.gz"]
    assert args({"transfer_pattern": "TransferLog*", "extended_pattern": "ExtendedAllLog*"}) == \
        ["--transfer-name", "TransferLog*", "--extended-name", "ExtendedAllLog*"]
    assert args({"transfer_pattern": "../../etc/passwd", "extended_pattern": None}) is None
    assert args({"transfer_pattern": "/etc/x", "extended_pattern": None}) is None


def _ext_line(ts: str, pid: int = 100) -> str:
    return f'{ts} 10.0.0.1 alice {pid} 257 MKD "/vod/d{pid}" - - "MKD d{pid}" ""\n'


def test_dateless_rotated_gz_filtered_by_line_time(daemon_mods, tmp_path):
    """ExtendedAllLog.1.gz 처럼 이름에 날짜가 없어도 줄 시각으로 기간을 거른다. 종료일은 그날 끝까지."""
    _, bulk = daemon_mods
    with gzip.open(tmp_path / "ExtendedAllLog.1.gz", "wt") as f:
        f.write(_ext_line("2026-11-09 23:59:59", 1))   # 시작일 전 → 제외
        f.write(_ext_line("2026-11-10 00:00:00", 2))
        f.write(_ext_line("2026-11-11 23:59:59", 3))   # 종료일 마지막 초 → 포함
        f.write(_ext_line("2026-11-12 00:00:00", 4))   # 종료일 다음 날 → 제외
    (tmp_path / "ExtendedAllLog").write_text(_ext_line("2026-11-11 12:00:00", 5))
    (tmp_path / "TransferLog").write_text("")

    date_from = bulk.parse_date("2026-11-10")
    date_to = bulk.parse_date("2026-11-11", end=True)
    got = list(bulk.iter_files(str(tmp_path / "ExtendedAllLog.*.gz"), bulk.parse_extended_log,
                               date_from, date_to))
    assert [e["session_id"] for e in got] == ["2", "3"]
    got_all = list(bulk.iter_files(str(tmp_path / "ExtendedAllLog*"), bulk.parse_extended_log,
                                   date_from, date_to))
    assert sorted(e["session_id"] for e in got_all) == ["2", "3", "5"]


def test_file_older_than_range_is_skipped(daemon_mods, tmp_path):
    _, bulk = daemon_mods
    p = tmp_path / "ExtendedAllLog.9.gz"
    with gzip.open(p, "wt") as f:
        f.write(_ext_line("2026-11-10 10:00:00"))
    old = time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1))
    os.utime(p, (old, old))       # 회전 시각이 기간보다 앞 → 풀지 않는다
    assert list(bulk.iter_file(str(p), bulk.parse_extended_log,
                               bulk.parse_date("2026-11-10"), None)) == []


def test_entry_time_parses_without_fromisoformat(daemon_mods):
    _, bulk = daemon_mods
    assert bulk._entry_time({"log_time": "2026-11-10T01:02:03+00:00"}) == \
        datetime(2026, 11, 10, 1, 2, 3, tzinfo=timezone.utc)
    assert bulk._entry_time({"log_time": "bad"}) is None
