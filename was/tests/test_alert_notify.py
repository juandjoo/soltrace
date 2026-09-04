"""알림 문안 — 같은 장애의 반복 발송을 막은 뒤 나가는 '복구' 알림이 제대로 구분되는지.

payload 생성 함수는 순수 함수(DB 불필요)라 dict 로 직접 호출한다.
"""
from datetime import datetime, timedelta, timezone

from app import notifier

KST = timezone(timedelta(hours=9))
BUCKET = datetime(2026, 9, 4, 17, 10, tzinfo=KST)
MB = 1024 * 1024


def alert(**kw):
    base = {
        "device_id": 1, "device_hostname": "acl-up-01", "group_name": "ACL 업로드 서버",
        "telco": "SK", "bucket": BUCKET, "metric": "throughput",
        "severity": "critical", "value": 5.28 * MB, "baseline": 121.13 * MB,
    }
    return {**base, **kw}


def recovery(**kw):
    return alert(value=None, baseline=None, recovered=True,
                 duration_min=40, alert_count=5, **kw)


# ── 이상 알림은 종전 그대로 ─────────────────────────────────────────────────

def test_impact_payload_unchanged():
    p = notifier._slack_payload([alert()])
    text = p["blocks"][0]["text"]["text"]
    assert "서비스 영향 감지 1건" in text
    assert "[SK]ACL 업로드 서버" in text
    assert "전송 속도 5.28 MB/s (평소 121.13 MB/s)" in text
    assert "복구" not in text


# ── 복구 알림 ───────────────────────────────────────────────────────────────

def test_recovery_slack_payload_says_recovered():
    p = notifier._slack_payload([recovery()])
    text = p["blocks"][0]["text"]["text"]
    assert "서비스 복구 1건" in text
    assert "[복구] *[SK]ACL 업로드 서버* — 전송 속도 정상 회복 (이상 40분·5건)" in text
    assert "영향 감지" not in text
    # 값이 없는 알림이므로 속도 포맷을 시도하면 안 된다(None 포맷 시 예외)
    assert "MB/s" not in text
    assert p["text"] == "[SolTrace] 서비스 복구 1건"


def test_recovery_does_not_carry_critical_tag():
    """등급은 '이상 기간 중 최고 등급'이라 심각으로 남지만, 복구 헤더에 붙이지 않는다."""
    text = notifier._slack_payload([recovery()])["blocks"][0]["text"]["text"]
    assert "심각 1건" not in text


def test_recovery_summary_line():
    s = notifier.build_summary(recovery())
    assert s.startswith("[복구] [SK]ACL 업로드 서버 — 전송 속도 정상 회복 (이상 40분·5건)")
    assert "2026-09-04 17:10 KST 마지막" in s


def test_recovery_generic_payload_type():
    p = notifier._generic_payload([recovery()])
    assert p["type"] == "service_recovery"
    assert p["alerts"][0]["recovered"] is True
    assert p["alerts"][0]["duration_min"] == 40
    assert notifier._generic_payload([alert()])["type"] == "service_impact"


def test_recovery_hms_body_and_subject(monkeypatch):
    sent = {}
    monkeypatch.setattr(notifier, "_post_json",
                        lambda url, payload, headers=None: sent.update(payload))
    notifier._hms_post("https://hms.example.com", "SK", [recovery()])
    assert sent["prop"]["subject"] == "[SolTrace] 서비스 복구 1건"
    body = sent["prop"]["body"]
    assert "정상으로 회복되었습니다" in body
    assert "정상 회복 (이상 40분·5건)" in body


def test_mixed_list_is_not_treated_as_recovery():
    """이상과 복구를 한 묶음으로 보내지 않는다 — 섞이면 이상 알림 형식을 따른다."""
    assert notifier._is_recovery([recovery(), alert()]) is False
    assert notifier._is_recovery([]) is False
