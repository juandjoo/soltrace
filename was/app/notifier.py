"""
서비스 이상 알림 발송 (메일 + 웹훅).
설정은 app_config 테이블(UI에서 변경 가능)에서 읽고, 없으면 .env 기본값 사용.
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
    "cwd_fail_spike": "CWD 실패 급증",
}


def _fmt_metric(metric: str, value: float) -> str:
    if metric == "throughput":
        return f"{value / (1024 * 1024):.2f} MB/s"
    if metric == "cwd_fail_spike":
        return f"{int(value)}건"
    return f"{value * 100:.1f}%"


def build_summary(alert: dict) -> str:
    sev = _SEVERITY_LABEL.get(alert["severity"], alert["severity"])
    metric = _METRIC_LABEL.get(alert["metric"], alert["metric"])
    base = alert.get("baseline")
    base_str = f" (평소 {_fmt_metric(alert['metric'], base)})" if base is not None else ""
    return (
        f"[{sev}] {alert['device_hostname']} — {metric} "
        f"{_fmt_metric(alert['metric'], alert['value'])}{base_str} "
        f"@ {alert['bucket']:%Y-%m-%d %H:%M} UTC"
    )


def _load_cfg(db) -> dict:
    """app_config에서 알림 설정 로드. DB 값이 우선, 없으면 .env 기본값."""
    from app.security import get_config
    def _g(key, default=""):
        return get_config(db, key) or default
    return {
        "smtp_host":     _g("notify_smtp_host",     settings.smtp_host),
        "smtp_port":     int(_g("notify_smtp_port", str(settings.smtp_port))),
        "smtp_user":     _g("notify_smtp_user",     settings.smtp_user),
        "smtp_password": _g("notify_smtp_password", settings.smtp_password),
        "smtp_from":     _g("notify_smtp_from",     settings.smtp_from),
        "smtp_tls":      _g("notify_smtp_tls",      "true" if settings.smtp_tls else "false").lower() != "false",
        "email_to":      _g("notify_email_to",      settings.alert_email_to),
        "webhook_url":   _g("notify_webhook_url",   settings.alert_webhook_url),
    }


def channels_configured(db) -> bool:
    cfg = _load_cfg(db)
    recipients = [a.strip() for a in cfg["email_to"].split(",") if a.strip()]
    email_ok = bool(cfg["smtp_host"] and cfg["smtp_from"] and recipients)
    return email_ok or bool(cfg["webhook_url"])


def _send_email(alerts: list[dict], cfg: dict) -> None:
    recipients = [a.strip() for a in cfg["email_to"].split(",") if a.strip()]
    if not (cfg["smtp_host"] and cfg["smtp_from"] and recipients):
        return
    lines = [build_summary(a) for a in alerts]
    msg = EmailMessage()
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    msg["Subject"] = f"[SolTrace] 서비스 영향 감지 {len(alerts)}건" + (f" (심각 {crit})" if crit else "")
    msg["From"] = cfg["smtp_from"]
    msg["To"] = ", ".join(recipients)
    msg.set_content("FTP 서버 부하로 인한 서비스 영향이 감지되었습니다.\n\n" + "\n".join(lines))
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=10) as srv:
        if cfg["smtp_tls"]:
            srv.starttls()
        if cfg["smtp_user"]:
            srv.login(cfg["smtp_user"], cfg["smtp_password"])
        srv.send_message(msg)
    log.info("Alert email sent: %d alert(s) → %s", len(alerts), msg["To"])


def _send_webhook(alerts: list[dict], cfg: dict) -> None:
    if not cfg["webhook_url"]:
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
        cfg["webhook_url"], data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()
    log.info("Alert webhook sent: %d alert(s)", len(alerts))


def dispatch(alerts: list[dict], db) -> bool:
    """메일·웹훅 발송. 한 채널이라도 보내면 True."""
    if not alerts:
        return False
    cfg = _load_cfg(db)
    sent = False
    for fn in (_send_email, _send_webhook):
        try:
            fn(alerts, cfg)
            sent = True
        except Exception as e:
            log.error("Alert dispatch via %s failed: %s", fn.__name__, e)
    return sent
