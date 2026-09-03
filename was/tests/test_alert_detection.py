"""이상 감지 판정 — 소량 파일 대량 전송이 전송 속도 알림을 내지 않는지 검증.

_eval_bucket 은 순수 함수(DB 불필요)라 버킷 행을 흉내낸 객체로 직접 호출한다.
"""
from types import SimpleNamespace

import pytest

from app import alert_settings
from app.service_monitor import ServiceMonitor

MB = 1024 * 1024


def bucket(*, transfers=0, fails=0, secs=0.0, nbytes=0,
           xfers_big=0, bytes_big=0, secs_big=0.0,
           logins=0, login_fails=0, cwd_fails=0):
    return SimpleNamespace(
        bucket="2026-09-03T10:00:00+00:00",
        transfers=transfers, transfer_fails=fails, bytes=nbytes, transfer_secs=secs,
        xfers_big=xfers_big, bytes_big=bytes_big, secs_big=secs_big,
        login_attempts=logins, login_fails=login_fails, cwd_fails=cwd_fails,
    )


def normal_bucket():
    """평소: 100MB 파일 10개를 각 2초에 전송 → 50MB/s"""
    return bucket(transfers=30, fails=0, nbytes=1000 * MB, secs=30,
                  xfers_big=10, bytes_big=1000 * MB, secs_big=20.0)


@pytest.fixture
def cfg():
    return alert_settings.defaults()


def metrics(alerts):
    return {a["metric"] for a in alerts}


def evaluate(cand, baseline, cfg):
    return ServiceMonitor._eval_bucket(1, cand, baseline, cfg)


# ── 핵심: 소량 파일 대량 업로드 오탐 ────────────────────────────────────────

def test_small_file_burst_does_not_trigger_throughput(cfg):
    """20KB 파일 3000개를 0.2초씩 — 전체 실효속도는 0.1MB/s 로 폭락하지만
    큰 파일 표본이 없으므로 전송 속도 알림이 뜨면 안 된다."""
    base = [normal_bucket() for _ in range(30)]
    burst = bucket(transfers=3000, fails=0, nbytes=3000 * 20 * 1024, secs=600.0,
                   xfers_big=0, bytes_big=0, secs_big=0.0)
    # 전체 기준 실효속도가 임계 아래인 것을 먼저 확인 (예전 로직이라면 알림 대상)
    assert burst.bytes / burst.transfer_secs < 50 * MB * (1 - cfg["throughput_drop"])
    assert "throughput" not in metrics(evaluate(burst, base, cfg))


def test_small_file_burst_mixed_with_large_files_uses_large_only(cfg):
    """소량 파일이 섞여도 큰 파일 속도가 정상이면 알림이 없어야 한다."""
    base = [normal_bucket() for _ in range(30)]
    mixed = bucket(transfers=3000, fails=0, nbytes=3000 * 20 * 1024 + 500 * MB, secs=620.0,
                   xfers_big=5, bytes_big=500 * MB, secs_big=10.0)   # 큰 파일은 50MB/s 유지
    assert "throughput" not in metrics(evaluate(mixed, base, cfg))


# ── 진짜 저하는 여전히 잡아야 한다 ──────────────────────────────────────────

def test_real_throughput_drop_still_alerts(cfg):
    """큰 파일 속도가 50MB/s → 5MB/s 로 떨어지면 알림."""
    base = [normal_bucket() for _ in range(30)]
    slow = bucket(transfers=30, fails=0, nbytes=1000 * MB, secs=300.0,
                  xfers_big=10, bytes_big=1000 * MB, secs_big=200.0)
    alerts = evaluate(slow, base, cfg)
    assert "throughput" in metrics(alerts)
    tp = next(a for a in alerts if a["metric"] == "throughput")
    assert tp["severity"] == "critical"
    assert tp["sample_count"] == 10       # 큰 파일 건수 기준으로 보고


def test_throughput_skipped_when_too_few_large_files(cfg):
    """큰 파일 표본이 최소 건수 미만이면 판정하지 않는다."""
    base = [normal_bucket() for _ in range(30)]
    few = bucket(transfers=200, fails=0, nbytes=210 * MB, secs=400.0,
                 xfers_big=cfg["min_large_samples"] - 1, bytes_big=10 * MB, secs_big=50.0)
    assert "throughput" not in metrics(evaluate(few, base, cfg))


def test_throughput_skipped_when_file_size_mix_differs(cfg):
    """평소 100MB 짜리를 보내던 장비가 5MB 짜리만 보내면 비교가 성립하지 않아 보류."""
    base = [normal_bucket() for _ in range(30)]   # 평균 100MB
    small_big = bucket(transfers=50, fails=0, nbytes=50 * MB, secs=40.0,
                       xfers_big=10, bytes_big=50 * MB, secs_big=25.0)  # 평균 5MB, 2MB/s
    assert "throughput" not in metrics(evaluate(small_big, base, cfg))


# ── 다른 지표는 그대로 동작 ─────────────────────────────────────────────────

def test_fail_rate_still_alerts_on_burst(cfg):
    """소량 파일 대량 전송이라도 실패율이 높으면 알림은 나가야 한다."""
    base = [normal_bucket() for _ in range(30)]
    burst = bucket(transfers=3000, fails=900, nbytes=3000 * 20 * 1024, secs=600.0)
    assert "fail_rate" in metrics(evaluate(burst, base, cfg))


def test_quiet_bucket_produces_no_alerts(cfg):
    base = [normal_bucket() for _ in range(30)]
    assert evaluate(normal_bucket(), base, cfg) == []


def test_thresholds_are_honoured(cfg):
    """임계값을 둔감하게 바꾸면 같은 버킷에서 알림이 사라진다(설정 페이지 반영 경로)."""
    base = [normal_bucket() for _ in range(30)]
    slow = bucket(transfers=30, fails=0, nbytes=1000 * MB, secs=300.0,
                  xfers_big=10, bytes_big=1000 * MB, secs_big=50.0)   # 20MB/s (60% 하락)
    assert "throughput" in metrics(evaluate(slow, base, cfg))         # 기본 50% 임계 → 알림
    lenient = {**cfg, "throughput_drop": 0.9}                          # 90% 이상 떨어져야 알림
    assert "throughput" not in metrics(evaluate(slow, base, lenient))
