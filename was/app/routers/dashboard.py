from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, DeviceGroup, FtpLog, Group
from app.schemas import (
    DashboardDetail, DashboardStats, TimeSeriesPoint, TopItem,
    HourlyPoint, GroupHourlySeries,
    ServiceHealthResponse, ServiceHealthDevice, ServiceAlertItem, ServiceTrendPoint, FailTotals,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardDetail)
def get_dashboard(
    days: int = Query(default=7, ge=1, le=366),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        since = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
        until = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
    else:
        since = now - timedelta(days=days)
        until = now
    days = max(1, (until - since).days or 1)

    base_q = db.query(FtpLog).filter(FtpLog.log_time >= since, FtpLog.log_time <= until)
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

    # ── By device group (장비그룹별 사용량) ───────────────────────────────────
    # 장비가 여러 그룹에 속하면 그룹마다 집계됨 (그룹 단위 사용량 관점)
    group_rows = (
        base_q.join(Device, FtpLog.device_id == Device.id)
        .join(DeviceGroup, DeviceGroup.device_id == Device.id)
        .join(Group, Group.id == DeviceGroup.group_id)
        .with_entities(
            Group.name,
            Group.customer,
            func.count().label("cnt"),
            func.coalesce(func.sum(FtpLog.file_size), 0).label("bytes"),
        )
        .group_by(Group.name, Group.customer)
        .order_by(func.coalesce(func.sum(FtpLog.file_size), 0).desc())
        .limit(10)
        .all()
    )
    top_groups = [TopItem(label=r.name, customer=r.customer, count=r.cnt, bytes=r.bytes) for r in group_rows]

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
        top_groups=top_groups,
        by_action=by_action,
    )


@router.get("/hourly", response_model=list[GroupHourlySeries])
def get_hourly(
    days: int = Query(default=7, ge=1, le=366),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        since = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
        until = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
    else:
        since = now - timedelta(days=days)
        until = now

    rows = db.execute(text("""
        SELECT g.id AS group_id, g.name, g.telco,
               DATE_TRUNC('hour', fl.log_time) AS bucket,
               COALESCE(SUM(CASE WHEN fl.action='upload'   THEN 1 ELSE 0 END), 0)::int AS uploads,
               COALESCE(SUM(CASE WHEN fl.action='download' THEN 1 ELSE 0 END), 0)::int AS downloads,
               COALESCE(SUM(CASE WHEN fl.action='upload'   THEN fl.file_size ELSE 0 END), 0)::int AS bytes_in,
               COALESCE(SUM(CASE WHEN fl.action='download' THEN fl.file_size ELSE 0 END), 0)::int AS bytes_out
        FROM ftp_logs fl
        JOIN devices d ON d.id = fl.device_id
        JOIN device_groups dg ON dg.device_id = d.id
        JOIN groups g ON g.id = dg.group_id
        WHERE fl.log_time >= :since AND fl.log_time <= :until
        GROUP BY g.id, g.name, g.telco, bucket
        ORDER BY g.name, bucket
    """), {"since": since, "until": until}).fetchall()

    groups: dict[int, dict] = {}
    for r in rows:
        if r.group_id not in groups:
            groups[r.group_id] = {"group_id": r.group_id, "name": r.name, "telco": r.telco, "data": []}
        groups[r.group_id]["data"].append(HourlyPoint(
            bucket=r.bucket.strftime("%Y-%m-%dT%H:00:00Z"),
            uploads=r.uploads, downloads=r.downloads,
            bytes_in=r.bytes_in, bytes_out=r.bytes_out,
        ))
    return [GroupHourlySeries(**v) for v in groups.values()]


_MB = 1024 * 1024


def _ratio(num, den):
    return (num / den) if den else None


@router.get("/service-health", response_model=ServiceHealthResponse)
def get_service_health(
    hours: int = Query(default=24, ge=1, le=8760),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
):
    """부하로 인한 서비스 영향도 — 장비별 최신 상태 + 최근 알림 + 추이."""
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        since = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
    else:
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
            m.login_attempts, m.login_fails, m.cwd_fails
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
               SUM(m.transfer_fails)::float / NULLIF(SUM(m.transfers), 0)    AS fail_rate,
               SUM(m.bytes)::float        / NULLIF(SUM(m.transfer_secs), 0)  AS tp,
               SUM(m.login_fails)::float  / NULLIF(SUM(m.login_attempts), 0) AS login_fail_rate,
               SUM(m.cwd_fails)::int                                          AS cwd_fails
        FROM service_metrics m
        WHERE m.bucket >= :since {dev_f}
        GROUP BY m.bucket
        ORDER BY m.bucket
    """), params).fetchall()
    trend = [ServiceTrendPoint(
        bucket=r.bucket, fail_rate=r.fail_rate,
        throughput_mb=(r.tp / _MB) if r.tp is not None else None,
        login_fail_rate=r.login_fail_rate,
        cwd_fails=r.cwd_fails,
    ) for r in trend_rows]

    totals_row = db.execute(text(f"""
        SELECT COALESCE(SUM(m.transfer_fails), 0)::int AS transfer_fails,
               COALESCE(SUM(m.login_fails), 0)::int    AS login_fails,
               COALESCE(SUM(m.cwd_fails), 0)::int      AS cwd_fails
        FROM service_metrics m
        WHERE m.bucket >= :since {dev_f}
    """), params).fetchone()
    fail_totals = FailTotals(
        transfer_fails=totals_row.transfer_fails,
        login_fails=totals_row.login_fails,
        cwd_fails=totals_row.cwd_fails,
    )

    return ServiceHealthResponse(devices=devices, alerts=alerts, trend=trend, fail_totals=fail_totals)
