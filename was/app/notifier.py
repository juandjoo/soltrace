"""
서비스 이상 알림 발송 (메일 + 웹훅).
설정은 app_config 테이블(UI에서 변경 가능)에서 읽고, 없으면 .env 기본값 사용.
"""
import json
import logging
import re
import smtplib
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape as _esc

from sqlalchemy import text

from app.config import settings

log = logging.getLogger("soltrace.notify")

_SEVERITY_LABEL = {"warning": "주의", "critical": "심각"}
_METRIC_LABEL = {
    "fail_rate": "전송 실패율",
    "throughput": "전송 속도",
    "login_fail_rate": "로그인 실패율",
    "cwd_fail_spike": "CWD 실패 급증",
}
_NOTIFY_KEYS = [
    "notify_smtp_host", "notify_smtp_port", "notify_smtp_user",
    "notify_smtp_password", "notify_smtp_from", "notify_smtp_tls",
    "notify_email_to", "notify_webhook_url",
    "notify_hms_url", "notify_hms_telco", "notify_hms_svc",
]
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


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
    """app_config 배치 조회 (1 query). DB 값 우선, 없으면 .env 기본값."""
    rows = {r[0]: r[1] for r in db.execute(
        text("SELECT key, value FROM app_config WHERE key = ANY(:keys)"),
        {"keys": _NOTIFY_KEYS},
    ).fetchall()}

    def _g(key, default=""):
        return rows.get(key) or default

    return {
        "smtp_host":     _g("notify_smtp_host",     settings.smtp_host),
        "smtp_port":     int(_g("notify_smtp_port", str(settings.smtp_port))),
        "smtp_user":     _g("notify_smtp_user",     settings.smtp_user),
        "smtp_password": _g("notify_smtp_password", settings.smtp_password),
        "smtp_from":     _g("notify_smtp_from",     settings.smtp_from),
        "smtp_tls":      _g("notify_smtp_tls", "true" if settings.smtp_tls else "false").lower() != "false",
        "email_to":      _g("notify_email_to",      settings.alert_email_to),
        "webhook_url":   _g("notify_webhook_url",   settings.alert_webhook_url),
        "hms_url":       _g("notify_hms_url",       settings.alert_hms_url),
        "hms_telco":     _g("notify_hms_telco",     settings.alert_hms_telco),
        "hms_svc":       _g("notify_hms_svc",       settings.alert_hms_svc),
    }


def validate_webhook_url(url: str) -> None:
    """http(s):// 외 스킴 차단."""
    if url and not _HTTP_RE.match(url):
        raise ValueError(f"웹훅 URL은 http:// 또는 https://로 시작해야 합니다: {url!r}")


def channels_configured(db) -> bool:
    cfg = _load_cfg(db)
    recipients = [a.strip() for a in cfg["email_to"].split(",") if a.strip()]
    email_ok = bool(cfg["smtp_host"] and cfg["smtp_from"] and recipients)
    return email_ok or bool(cfg["webhook_url"]) or bool(cfg["hms_url"])


def _send_email(alerts: list[dict], cfg: dict) -> bool:
    recipients = [a.strip() for a in cfg["email_to"].split(",") if a.strip()]
    if not (cfg["smtp_host"] and cfg["smtp_from"] and recipients):
        return False
    lines = [build_summary(a) for a in alerts]
    msg = EmailMessage()
    is_test = any(a.get("test") for a in alerts)
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    prefix = "[테스트] " if is_test else ""
    msg["Subject"] = f"{prefix}[SolTrace] 서비스 영향 감지 {len(alerts)}건" + (f" (심각 {crit})" if crit else "")
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
    return True


def _send_webhook(alerts: list[dict], cfg: dict) -> bool:
    if not cfg["webhook_url"]:
        return False
    is_test = any(a.get("test") for a in alerts)
    payload = {
        "source": "soltrace",
        "type": "service_impact",
        "test": is_test,
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
    return True


def _build_hms_body(alerts: list[dict]) -> str:
    rows = "".join(
        f"<tr><td style='padding:4px 8px'>{_esc(a['device_hostname'])}</td>"
        f"<td style='padding:4px 8px'>{_esc(_METRIC_LABEL.get(a['metric'], a['metric']))}</td>"
        f"<td style='padding:4px 8px'>{_esc(_SEVERITY_LABEL.get(a['severity'], a['severity']))}</td>"
        f"<td style='padding:4px 8px'>{_esc(_fmt_metric(a['metric'], a['value']))}</td>"
        f"<td style='padding:4px 8px'>{a['bucket']:%Y-%m-%d %H:%M} UTC</td></tr>"
        for a in alerts
    )
    return (
        "<html><body style='font-family:sans-serif;font-size:13px'>"
        "<p>FTP 서버 서비스 이상이 감지되었습니다.</p>"
        "<table border='1' cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
        "<thead><tr style='background:#f0f0f0'>"
        "<th style='padding:4px 8px'>장비</th><th style='padding:4px 8px'>지표</th>"
        "<th style='padding:4px 8px'>등급</th><th style='padding:4px 8px'>값</th>"
        "<th style='padding:4px 8px'>시각</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p style='color:#888;font-size:11px'>이 메일은 SolTrace에서 자동 발송되었습니다.</p>"
        "</body></html>"
    )


def _send_hms(alerts: list[dict], cfg: dict) -> bool:
    if not cfg["hms_url"]:
        return False
    is_test = any(a.get("test") for a in alerts)
    prefix = "[테스트] " if is_test else ""
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    subject = f"{prefix}[SolTrace] 서비스 영향 감지 {len(alerts)}건" + (f" (심각 {crit})" if crit else "")
    payload = {
        "telco_name": cfg["hms_telco"] or "SolTrace",
        "svc_list": [{"svc_name": cfg["hms_svc"] or "soltrace", "vol_list": None}],
        "alert_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "prop": {"subject": subject, "body": _build_hms_body(alerts)},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["hms_url"], data=data,
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()
    log.info("Alert HMS sent: %d alert(s)", len(alerts))
    return True


_CHANNEL_MAP = {
    "email":   _send_email,
    "webhook": _send_webhook,
    "hms":     _send_hms,
}


def dispatch(alerts: list[dict], db, channel: str = "all") -> bool:
    """메일·웹훅·HMS 발송. channel='all'이면 설정된 모든 채널, 아니면 지정 채널만."""
    if not alerts:
        return False
    cfg = _load_cfg(db)
    fns = list(_CHANNEL_MAP.values()) if channel == "all" else [_CHANNEL_MAP[channel]] if channel in _CHANNEL_MAP else []
    sent = False
    for fn in fns:
        try:
            if fn(alerts, cfg):
                sent = True
        except Exception as e:
            log.error("Alert dispatch via %s failed: %s", fn.__name__, e)
    return sent
