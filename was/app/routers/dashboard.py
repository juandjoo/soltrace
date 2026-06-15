from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Device, FtpLog
from app.schemas import DashboardDetail, DashboardStats, TimeSeriesPoint, TopItem

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
                func.case((FtpLog.action == "upload", 1), else_=0)
            ), 0).label("uploads"),
            func.coalesce(func.sum(
                func.case((FtpLog.action == "download", 1), else_=0)
            ), 0).label("downloads"),
            func.coalesce(func.sum(
                func.case((FtpLog.action == "delete", 1), else_=0)
            ), 0).label("deletes"),
            func.coalesce(func.sum(
                func.case((FtpLog.action == "upload", FtpLog.file_size), else_=0)
            ), 0).label("bytes_in"),
            func.coalesce(func.sum(
                func.case((FtpLog.action == "download", FtpLog.file_size), else_=0)
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
            func.coalesce(func.sum(func.case((FtpLog.action == "upload", 1), else_=0)), 0).label("uploads"),
            func.coalesce(func.sum(func.case((FtpLog.action == "download", 1), else_=0)), 0).label("downloads"),
            func.coalesce(func.sum(func.case((FtpLog.action == "delete", 1), else_=0)), 0).label("deletes"),
            func.coalesce(func.sum(func.case((FtpLog.action == "upload", FtpLog.file_size), else_=0)), 0).label("bytes_in"),
            func.coalesce(func.sum(func.case((FtpLog.action == "download", FtpLog.file_size), else_=0)), 0).label("bytes_out"),
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
