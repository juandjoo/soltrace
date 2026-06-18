"""
서비스 이상 알림 발송 (메일 + 웹훅).
설정은 app_config 테이블(UI에서 변경 가능)에서 읽고, 없으면 .env 기본값 사용.
"""
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape as _esc

from sqlalchemy import text

from app.config import settings
from app.security import get_config as _sec_get

log = logging.getLogger("soltrace.notify")

_SEVERITY_LABEL = {"warning": "주의", "critical": "심각"}
_METRIC_LABEL = {
    "fail_rate": "전송 실패율",
    "throughput": "전송 속도",
    "login_fail_rate": "로그인 실패율",
    "cwd_fail_spike": "CWD 실패 급증",
}
_NOTIFY_KEYS = [
    "notify_webhook_url",
    "notify_hms_url",
]
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


def _fmt_metric(metric: str, value: float) -> str:
    if metric == "throughput":
        return f"{value / (1024 * 1024):.2f} MB/s"
    if metric == "cwd_fail_spike":
        return f"{int(value)}건"
    return f"{value * 100:.1f}%"


_KST = timedelta(hours=9)


def _alert_label(alert: dict) -> str:
    """[텔코]그룹명 형식. 텔코·그룹 없으면 device_hostname으로 폴백."""
    telco = alert.get("telco") or ""
    group = alert.get("group_name") or ""
    if group:
        return f"[{telco}]{group}" if telco else group
    return alert.get("device_hostname", "unknown")


def build_summary(alert: dict) -> str:
    sev = _SEVERITY_LABEL.get(alert["severity"], alert["severity"])
    metric = _METRIC_LABEL.get(alert["metric"], alert["metric"])
    base = alert.get("baseline")
    base_str = f" (평소 {_fmt_metric(alert['metric'], base)})" if base is not None else ""
    kst = alert["bucket"] + _KST
    return (
        f"[{sev}] {_alert_label(alert)} — {metric} "
        f"{_fmt_metric(alert['metric'], alert['value'])}{base_str} "
        f"@ {kst:%Y-%m-%d %H:%M} KST"
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
        "webhook_url": _g("notify_webhook_url", settings.alert_webhook_url),
        "hms_url":     _g("notify_hms_url",     settings.alert_hms_url),
    }


def validate_webhook_url(url: str) -> None:
    """http(s):// 외 스킴 차단."""
    if url and not _HTTP_RE.match(url):
        raise ValueError(f"웹훅 URL은 http:// 또는 https://로 시작해야 합니다: {url!r}")


def is_muted(db) -> bool:
    return (_sec_get(db, "notify_muted") or "false").lower() == "true"


def channels_configured(db) -> bool:
    cfg = _load_cfg(db)
    return bool(cfg["webhook_url"]) or bool(cfg["hms_url"])



def _slack_payload(alerts: list[dict]) -> dict:
    """Slack Incoming Webhook 포맷."""
    is_test = any(a.get("test") for a in alerts)
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    test_tag = " _[테스트]_" if is_test else ""
    crit_tag = f"  :rotating_light: 심각 {crit}건" if crit else ""
    header = f":bell: *SolTrace 서비스 영향 감지 {len(alerts)}건*{crit_tag}{test_tag}"
    lines = []
    for a in alerts:
        icon = ":rotating_light:" if a["severity"] == "critical" else ":warning:"
        sev = _SEVERITY_LABEL.get(a["severity"], a["severity"])
        metric = _METRIC_LABEL.get(a["metric"], a["metric"])
        val = _fmt_metric(a["metric"], a["value"])
        base = a.get("baseline")
        base_str = f" (평소 {_fmt_metric(a['metric'], base)})" if base is not None else ""
        kst = (a["bucket"] + _KST).strftime("%m/%d %H:%M")
        lines.append(f"{icon} [{sev}] *{_alert_label(a)}* — {metric} {val}{base_str} · {kst} KST")
    text = header + "\n" + "\n".join(lines)
    return {
        "text": f"[SolTrace] 서비스 영향 감지 {len(alerts)}건",
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }


def _generic_payload(alerts: list[dict]) -> dict:
    is_test = any(a.get("test") for a in alerts)
    return {
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


def _send_webhook(alerts: list[dict], cfg: dict, db=None) -> bool:
    if not cfg["webhook_url"]:
        return False
    url = cfg["webhook_url"]
    is_slack = "hooks.slack.com" in url
    payload = _slack_payload(alerts) if is_slack else _generic_payload(alerts)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()
    log.info("Alert webhook sent: %d alert(s) (slack=%s)", len(alerts), is_slack)
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


def _hms_post(url: str, telco: str, alerts: list[dict]) -> None:
    is_test = any(a.get("test") for a in alerts)
    prefix = "[테스트] " if is_test else ""
    crit = sum(1 for a in alerts if a["severity"] == "critical")
    subject = f"{prefix}[SolTrace] 서비스 영향 감지 {len(alerts)}건" + (f" (심각 {crit})" if crit else "")
    payload = {
        "telco_name": telco,
        "svc_list": [{"svc_name": "soltrace", "vol_list": None}],
        "alert_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "prop": {"subject": subject, "body": _build_hms_body(alerts)},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "X-reqsite": "hermesweb",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()


def _send_hms(alerts: list[dict], cfg: dict, db=None) -> bool:
    if not cfg["hms_url"]:
        return False
    # _enrich_alerts()가 이미 telco를 주입 — 미지정은 "SolTrace" 처리
    by_telco: dict[str, list] = {}
    for a in alerts:
        telco = a.get("telco") or "SolTrace"
        by_telco.setdefault(telco, []).append(a)
    sent = False
    for telco, group in by_telco.items():
        _hms_post(cfg["hms_url"], telco, group)
        log.info("Alert HMS sent: %d alert(s) → telco=%s", len(group), telco)
        sent = True
    return sent


_CHANNEL_MAP = {
    "webhook": _send_webhook,
    "hms":     _send_hms,
}


def _enrich_alerts(alerts: list[dict], db) -> list[dict]:
    """device_id → (group_name, telco) 일괄 조회 후 alert에 주입. 이미 있으면 스킵."""
    ids = list({a["device_id"] for a in alerts if a.get("device_id") is not None})
    if not ids:
        return alerts
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (dg.device_id) dg.device_id, g.name, g.telco "
            "FROM device_groups dg JOIN groups g ON g.id = dg.group_id "
            "WHERE dg.device_id = ANY(:ids) "
            "ORDER BY dg.device_id, (g.telco IS NOT NULL) DESC, g.id"
        ),
        {"ids": ids},
    ).fetchall()
    info = {r[0]: (r[1], r[2]) for r in rows}
    enriched = []
    for a in alerts:
        if "group_name" not in a:
            gname, telco = info.get(a.get("device_id"), (None, None))
            a = {**a, "group_name": gname, "telco": telco}
        enriched.append(a)
    return enriched


def dispatch(alerts: list[dict], db, channel: str = "all") -> bool:
    """메일·웹훅·HMS 발송. channel='all'이면 설정된 모든 채널, 아니면 지정 채널만.
    notify_muted=true 이면 테스트 발송이 아닌 한 모두 건너뜀."""
    if not alerts:
        return False
    is_test = any(a.get("test") for a in alerts)
    if not is_test and is_muted(db):
        log.info("Notifications are muted — skipping dispatch")
        return False
    alerts = _enrich_alerts(alerts, db)
    cfg = _load_cfg(db)
    fns = list(_CHANNEL_MAP.values()) if channel == "all" else [_CHANNEL_MAP[channel]] if channel in _CHANNEL_MAP else []
    sent = False
    for fn in fns:
        try:
            if fn(alerts, cfg, db=db):
                sent = True
        except Exception as e:
            log.error("Alert dispatch via %s failed: %s", fn.__name__, e)
    return sent
