"""데몬 하트비트 끊김 판정 — 장비 화면·대시보드·알림이 같이 쓰는 규칙 (app/device_health)."""
from datetime import datetime, timedelta, timezone

import pytest

from app import device_health as dh
from app import notifier
from app.service_monitor import HEARTBEAT_METRIC, ServiceMonitor

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
FRESH = NOW - timedelta(seconds=60)
STALE = NOW - timedelta(minutes=30)


@pytest.mark.parametrize("status,hb,expected", [
    ("running", FRESH, None),
    ("stopping", FRESH, None),          # 막 내려간 참 — 재시작이면 곧 돌아온다
    ("stopping", STALE, dh.STOPPED),    # 종료중으로 멈춘 채 며칠 — 이번 사고
    ("running", STALE, dh.LOST),
    ("degraded", STALE, dh.LOST),
    (None, None, dh.LOST),              # 등록만 되고 하트비트가 한 번도 없다
    ("disabled", STALE, None),          # WAS 에서 일부러 끈 장비
])
def test_heartbeat_problem(status, hb, expected):
    assert dh.heartbeat_problem(status, hb, NOW) == expected


def test_stale_boundary_is_120s():
    assert dh.heartbeat_problem("running", NOW - timedelta(seconds=120), NOW) is None
    assert dh.heartbeat_problem("running", NOW - timedelta(seconds=121), NOW) == dh.LOST


def test_effective_status_keeps_reported_value_when_alive():
    assert dh.effective_daemon_status("degraded", FRESH, NOW) == "degraded"
    assert dh.effective_daemon_status("stopping", STALE, NOW) == dh.STOPPED
    assert dh.effective_daemon_status(None, FRESH, NOW) == "unknown"


def test_heartbeat_uses_its_own_streak():
    cfg = {"min_streak_buckets": 2, "min_streak_throughput": 3, "min_streak_heartbeat": 4}
    assert ServiceMonitor._min_streak(cfg, HEARTBEAT_METRIC) == 4
    assert ServiceMonitor._min_streak(cfg, "fail_rate") == 2


def test_retry_then_notify_once():
    """재시도 후 알림 — min_streak 번째 확인에서 한 번만 나간다."""
    send = [ServiceMonitor._should_notify(n, "critical" if n else None, "critical", 2) for n in range(4)]
    assert send == [False, True, False, False]


class _Row:
    def __init__(self, status, hb):
        self.id, self.daemon_status, self.last_heartbeat = 7, status, hb


def test_heartbeat_alert_message():
    a = ServiceMonitor._heartbeat_alert(_Row("stopping", STALE), dh.STOPPED, NOW, NOW)
    assert a["metric"] == HEARTBEAT_METRIC and a["severity"] == "critical"
    assert a["value"] == 30.0
    assert "종료중" in a["message"] and "proftpd" in a["message"]
    summary = notifier.build_summary({
        "device_hostname": "ftp-01", "bucket": NOW, "metric": a["metric"],
        "severity": a["severity"], "value": a["value"], "baseline": None,
    })
    assert "데몬 하트비트 30분째 없음" in summary and "[심각]" in summary


def test_current_bucket_aligns_to_epoch():
    m = ServiceMonitor.__new__(ServiceMonitor)
    m._bucket = timedelta(minutes=5)
    b = m._current_bucket(datetime(2026, 11, 16, 12, 7, 31, tzinfo=timezone.utc))
    assert b == datetime(2026, 11, 16, 12, 5, tzinfo=timezone.utc)
