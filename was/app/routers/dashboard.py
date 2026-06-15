from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, FtpLog
from app.schemas import (
    DashboardDetail, DashboardStats, TimeSeriesPoint, TopItem,
    ServiceHealthResponse, ServiceHealthDevice, ServiceAlertItem, ServiceTrendPoint,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardDetail)
def get_dashboard(
    days: int = Query(default=7, ge=1, le=90),
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    base_q = db.query(FtpLog).filter(FtpLog.log_time >= since)
    if device_id:
        base_q = base_q.filter(FtpLog.device_id == device_id)

    # ── Summary stats ────────────────────────────────────────────────────────
    agg = (
        base_q.with_entities(
            func.count().label("total"),
            func.coalesce(func.sum(
                case((FtpLog.action == "upload", 1), else_=0)
            ), 0).label("uploads"),
            func.coalesce(func.sum(
                case((FtpLog.action == "download", 1), else_=0)
            ), 0).label("downloads"),
            func.coalesce(func.sum(
                case((FtpLog.action == "delete", 1), else_=0)
            ), 0).label("deletes"),
            func.coalesce(func.sum(
                case((FtpLog.action == "upload", FtpLog.file_size), else_=0)
            ), 0).label("bytes_in"),
            func.coalesce(func.sum(
                case((FtpLog.action == "download", FtpLog.file_size), else_=0)
            ), 0).label("bytes_out"),
            func.count(func.distinct(FtpLog.username)).label("active_users"),
            func.count(func.distinct(FtpLog.device_id)).label("active_devices"),
        )
        .first()
    )

    stats = DashboardStats(
        total_uploads=agg.uploads,
        total_downloads=agg.downloads,
        total_deletes=agg.deletes,
        total_bytes_in=agg.bytes_in,
        total_bytes_out=agg.bytes_out,
        active_devices=agg.active_devices,
        active_users=agg.active_users,
        period_days=days,
    )

    # ── Time series (daily) ──────────────────────────────────────────────────
    ts_rows = (
        base_q.with_entities(
            func.date_trunc("day", FtpLog.log_time).label("day"),
            func.coalesce(func.sum(case((FtpLog.action == "upload", 1), else_=0)), 0).label("uploads"),
            func.coalesce(func.sum(case((FtpLog.action == "download", 1), else_=0)), 0).label("downloads"),
            func.coalesce(func.sum(case((FtpLog.action == "delete", 1), else_=0)), 0).label("deletes"),
            func.coalesce(func.sum(case((FtpLog.action == "upload", FtpLog.file_size), else_=0)), 0).label("bytes_in"),
            func.coalesce(func.sum(case((FtpLog.action == "download", FtpLog.file_size), else_=0)), 0).label("bytes_out"),
        )
        .group_by(text("day"))
        .order_by(text("day"))
        .all()
    )
    timeseries = [
        TimeSeriesPoint(
            date=row.day.strftime("%Y-%m-%d"),
            uploads=row.uploads,
            downloads=row.downloads,
            deletes=row.deletes,
            bytes_in=row.bytes_in,
            bytes_out=row.bytes_out,
        )
        for row in ts_rows
    ]

    # ── Top users ────────────────────────────────────────────────────────────
    top_user_rows = (
        base_q.filter(FtpLog.username.isnot(None))
        .with_entities(
            FtpLog.username,
            func.count().label("cnt"),
            func.coalesce(func.sum(FtpLog.file_size), 0).label("bytes"),
        )
        .group_by(FtpLog.username)
        .order_by(func.count().desc())
        .limit(10)
        .all()
    )
    top_users = [TopItem(label=r.username, count=r.cnt, bytes=r.bytes) for r in top_user_rows]

    # ── Top devices ──────────────────────────────────────────────────────────
    top_dev_rows = (
        base_q.join(Device, FtpLog.device_id == Device.id)
        .with_entities(
            Device.hostname,
            func.count().label("cnt"),
            func.coalesce(func.sum(FtpLog.file_size), 0).label("bytes"),
        )
        .group_by(Device.hostname)
        .order_by(func.count().desc())
        .limit(10)
        .all()
    )
    top_devices = [TopItem(label=r.hostname, count=r.cnt, bytes=r.bytes) for r in top_dev_rows]

    # ── By action ────────────────────────────────────────────────────────────
    action_rows = (
        base_q.with_entities(FtpLog.action, func.count().label("cnt"))
        .group_by(FtpLog.action)
        .all()
    )
    by_action = {r.action: r.cnt for r in action_rows}

    return DashboardDetail(
        stats=stats,
        timeseries=timeseries,
        top_users=top_users,
        top_devices=top_devices,
        by_action=by_action,
    )


_MB = 1024 * 1024


def _ratio(num, den):
    return (num / den) if den else None


@router.get("/service-health", response_model=ServiceHealthResponse)
def get_service_health(
    hours: int = Query(default=24, ge=1, le=168),
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """부하로 인한 서비스 영향도 — 장비별 최신 상태 + 최근 알림 + 추이."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    params = {"since": since, "did": device_id}
    dev_f = "AND m.device_id = :did" if device_id else ""
    adev_f = "AND a.device_id = :did" if device_id else ""

    # 최근 알림 (장비 상태 판정에도 사용)
    alert_rows = db.execute(text(f"""
        SELECT a.id, a.device_id, d.hostname, a.bucket, a.metric, a.severity,
               a.value, a.baseline, a.message, a.created_at
        FROM service_alerts a JOIN devices d ON d.id = a.device_id
        WHERE a.created_at >= :since {adev_f}
        ORDER BY a.created_at DESC
        LIMIT 200
    """), params).fetchall()

    alerts = [ServiceAlertItem(
        id=r.id, device_id=r.device_id, hostname=r.hostname, bucket=r.bucket,
        metric=r.metric, severity=r.severity, value=r.value, baseline=r.baseline,
        message=r.message, created_at=r.created_at,
    ) for r in alert_rows]

    # 장비별 알림 집계 (상태 등급 + 미해결 건수)
    sev_rank = {"warning": 1, "critical": 2}
    dev_sev: dict[int, int] = {}
    dev_open: dict[int, int] = {}
    for r in alert_rows:
        dev_sev[r.device_id] = max(dev_sev.get(r.device_id, 0), sev_rank.get(r.severity, 1))
        dev_open[r.device_id] = dev_open.get(r.device_id, 0) + 1

    # 장비별 최신 닫힌 버킷 스냅샷
    snap_rows = db.execute(text(f"""
        SELECT DISTINCT ON (m.device_id)
            m.device_id, d.hostname, m.bucket,
            m.transfers, m.transfer_fails, m.bytes, m.transfer_secs,
            m.login_attempts, m.login_fails
        FROM service_metrics m JOIN devices d ON d.id = m.device_id
        WHERE m.bucket >= :since {dev_f}
        ORDER BY m.device_id, m.bucket DESC
    """), params).fetchall()

    rank_status = {0: "ok", 1: "warning", 2: "critical"}
    devices = []
    for r in snap_rows:
        tp = _ratio(r.bytes, r.transfer_secs)
        devices.append(ServiceHealthDevice(
            device_id=r.device_id, hostname=r.hostname, last_bucket=r.bucket,
            status=rank_status[dev_sev.get(r.device_id, 0)],
            fail_rate=_ratio(r.transfer_fails, r.transfers),
            throughput_mb=(tp / _MB) if tp is not None else None,
            login_fail_rate=_ratio(r.login_fails, r.login_attempts),
            open_alerts=dev_open.get(r.device_id, 0),
        ))
    # 알림은 있으나 최신 스냅샷이 없는 장비도 노출
    seen = {d.device_id for d in devices}
    for r in alert_rows:
        if r.device_id not in seen:
            seen.add(r.device_id)
            devices.append(ServiceHealthDevice(
                device_id=r.device_id, hostname=r.hostname,
                status=rank_status[dev_sev.get(r.device_id, 0)],
                open_alerts=dev_open.get(r.device_id, 0),
            ))
    devices.sort(key=lambda d: (-sev_rank.get(d.status, 0) if d.status in sev_rank else 0,
                                -(d.fail_rate or 0)))

    # 추이 (전체 또는 선택 장비)
    trend_rows = db.execute(text(f"""
        SELECT m.bucket,
               SUM(m.transfer_fails)::float / NULLIF(SUM(m.transfers), 0)   AS fail_rate,
               SUM(m.bytes)::float        / NULLIF(SUM(m.transfer_secs), 0) AS tp,
               SUM(m.login_fails)::float  / NULLIF(SUM(m.login_attempts), 0) AS login_fail_rate
        FROM service_metrics m
        WHERE m.bucket >= :since {dev_f}
        GROUP BY m.bucket
        ORDER BY m.bucket
    """), params).fetchall()
    trend = [ServiceTrendPoint(
        bucket=r.bucket, fail_rate=r.fail_rate,
        throughput_mb=(r.tp / _MB) if r.tp is not None else None,
        login_fail_rate=r.login_fail_rate,
    ) for r in trend_rows]

    return ServiceHealthResponse(devices=devices, alerts=alerts, trend=trend)
