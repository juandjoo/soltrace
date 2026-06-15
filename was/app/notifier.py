"""
서비스 이상 알림 발송 (메일 + 웹훅).
설정이 비어 있으면 해당 채널은 조용히 건너뛴다.
"""
import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("soltrace.notify")

_SEVERITY_LABEL = {"warning": "주의", "critical": "심각"}
_METRIC_LABEL = {
    "fail_rate": "전송 실패율",
    "throughput": "전송 속도",
    "login_fail_rate": "로그인 실패율",
}


def _fmt_metric(metric: str, value: float) -> str:
    if metric == "throughput":
        return f"{value / (1024 * 1024):.2f} MB/s"
    return f"{value * 100:.1f}%"


def build_summary(alert: dict) -> str:
    """alert dict(device_hostname, metric, severity, value, baseline, bucket) → 한 줄 요약."""
    sev = _SEVERITY_LABEL.get(alert["severity"], alert["severity"])
    metric = _METRIC_LABEL.get(alert["metric"], alert["metric"])
    base = alert.get("baseline")
    base_str = f" (평소 {_fmt_metric(alert['metric'], base)})" if base is not None else ""
    return (
        f"[{sev}] {alert['device_hostname']} — {metric} "
        f"{_fmt_metric(alert['metric'], alert['value'])}{base_str} "
        f"@ {alert['bucket']:%Y-%m-%d %H:%M} UTC"
    )


def channels_configured() -> bool:
    """메일 또는 웹훅 중 하나라도 설정돼 있으면 True."""
    email_ok = bool(settings.smtp_host and settings.smtp_from and settings.alert_email_recipients)
    return email_ok or bool(settings.alert_webhook_url)


def _send_email(alerts: list[dict]) -> None:
    if not (settings.smtp_host and settings.smtp_from and settings.alert_email_recipients):
        return
    lines = [build_summary(a) for a in alerts]
    msg = EmailMessage()
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    msg["Subject"] = f"[SolTrace] 서비스 영향 감지 {len(alerts)}건" + (f" (심각 {crit})" if crit else "")
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(settings.alert_email_recipients)
    msg.set_content("FTP 서버 부하로 인한 서비스 영향이 감지되었습니다.\n\n" + "\n".join(lines))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as srv:
        if settings.smtp_tls:
            srv.starttls()
        if settings.smtp_user:
            srv.login(settings.smtp_user, settings.smtp_password)
        srv.send_message(msg)
    log.info("Alert email sent: %d alert(s) → %s", len(alerts), msg["To"])


def _send_webhook(alerts: list[dict]) -> None:
    if not settings.alert_webhook_url:
        return
    payload = {
        "source": "soltrace",
        "type": "service_impact",
        "count": len(alerts),
        "alerts": [
            {
                "device": a["device_hostname"],
                "device_id": a["device_id"],
                "metric": a["metric"],
                "severity": a["severity"],
                "value": a["value"],
                "baseline": a.get("baseline"),
                "bucket": a["bucket"].isoformat(),
                "summary": build_summary(a),
            }
            for a in alerts
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        settings.alert_webhook_url, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (설정된 내부 URL)
        resp.read()
    log.info("Alert webhook sent: %d alert(s)", len(alerts))


def dispatch(alerts: list[dict]) -> bool:
    """메일·웹훅 발송. 한 채널이라도 보내면 True. 채널 미설정/실패는 격리."""
    if not alerts:
        return False
    sent = False
    for channel in (_send_email, _send_webhook):
        try:
            channel(alerts)
            sent = True
        except Exception as e:
            log.error("Alert dispatch via %s failed: %s", channel.__name__, e)
    return sent
