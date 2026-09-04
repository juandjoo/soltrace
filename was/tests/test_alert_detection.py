"""이상 감지 판정 — 소량 파일 대량 전송이 전송 속도 알림을 내지 않는지 검증.

_eval_bucket 은 순수 함수(DB 불필요)라 버킷 행을 흉내낸 객체로 직접 호출한다.
"""
from types import SimpleNamespace

import pytest

from app import alert_settings
from app.service_monitor import (ServiceMonitor, _like_patterns, cwd_not_ignored_sql,
                                 cwd_probe_sql, cwd_real_fail_sql)

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


# ── 사용자별 대역폭 차이 (장비 합산 속도의 정상 변동) ───────────────────────

def slow_user_bucket():
    """느린 사용자 혼자 올리는 구간: 100MB 파일 10개를 각 25초 → 4MB/s"""
    return bucket(transfers=10, fails=0, nbytes=1000 * MB, secs=250.0,
                  xfers_big=10, bytes_big=1000 * MB, secs_big=250.0)


def test_slow_user_is_normal_when_baseline_has_slow_buckets(cfg):
    """평소에도 느린 구간이 있으면(사용자별 회선 차이) 그 수준의 속도는 알리지 않는다.

    baseline 하위 5%가 4MB/s 인 장비에서 4MB/s 는 median 대비 92% 하락이지만
    '느린 사용자가 올리는 평소 모습'이므로 알림 대상이 아니다.
    """
    base = [normal_bucket() for _ in range(80)] + [slow_user_bucket() for _ in range(20)]
    assert "throughput" not in metrics(evaluate(slow_user_bucket(), base, cfg))


def test_slow_user_still_alerts_when_baseline_is_uniform(cfg):
    """평소 느린 구간이 없던 장비는 종전대로 민감하게 잡는다."""
    base = [normal_bucket() for _ in range(100)]     # 전 구간 50MB/s
    assert "throughput" in metrics(evaluate(slow_user_bucket(), base, cfg))


def test_below_the_slow_end_still_alerts(cfg):
    """평소 느린 구간이 있어도 그보다 훨씬 더 느리면 알린다(진짜 저하)."""
    base = [normal_bucket() for _ in range(80)] + [slow_user_bucket() for _ in range(20)]
    dead = bucket(transfers=10, fails=0, nbytes=1000 * MB, secs=2000.0,
                  xfers_big=10, bytes_big=1000 * MB, secs_big=2000.0)   # 0.5MB/s
    alerts = evaluate(dead, base, cfg)
    assert "throughput" in metrics(alerts)
    assert next(a for a in alerts if a["metric"] == "throughput")["severity"] == "critical"


def test_slow_percentile_needs_enough_baseline_buckets(cfg):
    """표본이 적으면 하위 분위는 그냥 최솟값이라 쓰지 않는다(비율 임계로만 판정)."""
    base = ([normal_bucket() for _ in range(8)] + [slow_user_bucket()])   # 9개 < 20
    assert "throughput" in metrics(evaluate(slow_user_bucket(), base, cfg))


def test_slow_percentile_is_configurable(cfg):
    """분위를 내릴수록 둔감해진다 — 임계가 더 느린 버킷으로 내려가기 때문(설정 반영 경로).

    느린 버킷이 10%인 baseline 에서 하위 15%는 평소(50MB/s) 구간을 가리키므로 임계가 높고,
    하위 5%는 느린 구간(4MB/s)을 가리켜 같은 속도가 '평소'로 인정된다.
    """
    base = [normal_bucket() for _ in range(90)] + [slow_user_bucket() for _ in range(10)]
    cand = slow_user_bucket()
    strict = {**cfg, "throughput_slow_pct": 0.15}
    assert "throughput" in metrics(evaluate(cand, base, strict))
    lenient = {**cfg, "throughput_slow_pct": 0.05}
    assert "throughput" not in metrics(evaluate(cand, base, lenient))


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
                  xfers_big=10, bytes_big=1000 * MB, secs_big=100.0)  # 10MB/s (80% 하락)
    assert "throughput" in metrics(evaluate(slow, base, cfg))         # 기본 60% 임계 → 알림
    lenient = {**cfg, "throughput_drop": 0.9}                          # 90% 이상 떨어져야 알림
    assert "throughput" not in metrics(evaluate(slow, base, lenient))


# ── CWD 실패 제외 경로 ───────────────────────────────────────────────────────

def test_cwd_ignore_patterns_empty_means_no_exclusion():
    """빈 설정은 빈 목록 → LIKE ANY(ARRAY[]) 가 false 라 전부 집계된다."""
    assert _like_patterns("") == []
    assert _like_patterns(None) == []
    assert _like_patterns("\n   \n") == []


def test_cwd_ignore_patterns_wildcard_and_escaping():
    """'*' 만 와일드카드로 쓰고, LIKE 메타문자는 그대로 매칭되도록 이스케이프한다."""
    assert _like_patterns("/upload/tmp/*") == ["/upload/tmp/%"]
    assert _like_patterns("  /backup  ") == ["/backup"]
    # '_' 는 LIKE 에서 '임의의 한 글자'라 그대로 두면 /aXb 까지 걸린다
    assert _like_patterns("/a_b") == ["/a\\_b"]
    assert _like_patterns("100%*") == ["100\\%%"]


def test_cwd_ignore_patterns_multiline():
    assert _like_patterns("/a/*\n/b") == ["/a/%", "/b"]


def test_cwd_probe_allows_small_clock_skew():
    """같은 흐름의 MKD 가 1~2초 앞선 시각으로 기록돼도 '존재 확인'으로 본다.

    엄격히 '이후'만 보면 CWD 550 → MKD 흐름이 초 단위 역전만으로 진짜 실패로 남는다.
    """
    sql = cwd_probe_sql("fl", ":since", ":until")
    assert "fl.log_time - INTERVAL '5 seconds'" in sql
    assert "(:since) - INTERVAL '5 seconds'" in sql   # 기간 프루닝 경계도 같이 넓힌다


def test_cwd_ignore_rule_lives_in_one_place():
    """제외 조건은 cwd_not_ignored_sql() 한 곳에만 둔다.

    롤업(service_metrics) · 알림 경로 분석 · 대시보드 실패 건수가 각자 조건을 적어두면
    "제외 경로를 설정했는데 화면 숫자만 그대로" 같은 어긋남이 생긴다.
    """
    import inspect

    from app.routers import dashboard
    from app import service_monitor

    assert cwd_not_ignored_sql() == cwd_not_ignored_sql("file_path")
    assert "fl.file_path" in cwd_not_ignored_sql("fl.file_path")
    # 진짜 실패 조건 = 제외 경로 + 존재 확인(직후 같은 경로 생성) 제외
    real = cwd_real_fail_sql("fl", ":since", ":until")
    assert cwd_not_ignored_sql("fl.file_path") in real
    assert "mk.action = 'mkdir'" in real and "mk.file_path = fl.file_path" in real

    for mod in (dashboard, service_monitor):
        src = inspect.getsource(mod)
        # 조건 문자열을 직접 적은 곳이 있으면(헬퍼 정의 제외) 기준이 갈라진 것
        literal = src.count("LIKE ANY(CAST(:cwd_ignore")
        expected = 1 if mod is service_monitor else 0  # 헬퍼 정의 1회
        assert literal == expected, f"{mod.__name__}: 제외 조건을 직접 적지 말고 헬퍼를 쓰세요"
        assert "cwd_not_ignored_sql(" in src
        # 실패 건수를 세는 곳은 '진짜 실패' 조건을 쓴다 (존재 확인 건이 다시 새어 들어오지 않게)
        assert "cwd_real_fail_sql(" in src
