from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey,
    Identity, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class AppConfig(Base):
    """전역 설정 키-값 저장소 (관리자 비밀번호 해시 등)."""
    __tablename__ = "app_config"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Telco(Base):
    """통신사 목록 (그룹의 telco 유형에서 선택)."""
    __tablename__ = "telcos"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45))
    device_key = Column(String(64), unique=True, nullable=False)
    status = Column(String(20), default="pending")
    os_info = Column(Text)
    kernel_version = Column(String(100))
    proftpd_version = Column(String(50))
    daemon_version = Column(String(20))
    last_heartbeat = Column(DateTime(timezone=True))
    daemon_status = Column(String(20), default="unknown")
    last_send_time = Column(DateTime(timezone=True))
    buffer_lines = Column(Integer, default=0)
    queue_size = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    error_message = Column(Text)
    cpu_percent = Column(Float)
    mem_mb = Column(Float)
    disk_free_gb = Column(Float)
    daemon_uptime = Column(Integer)
    update_requested = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    groups = relationship("Group", secondary="device_groups", back_populates="devices")
    # passive_deletes=True: 장비 삭제 시 자식 로그를 ORM이 로드/UPDATE하지 않고
    # DB의 ON DELETE CASCADE에 위임 (대량 로그 NOT NULL UPDATE 실패/성능 저하 방지)
    logs = relationship(
        "FtpLog", back_populates="device", lazy="dynamic", passive_deletes=True
    )


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    telco = Column(String(100))
    customer = Column(Text)
    upload_domains = Column(Text)
    auth = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)

    devices = relationship("Device", secondary="device_groups", back_populates="groups")


class DeviceGroup(Base):
    __tablename__ = "device_groups"

    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)


class FtpLog(Base):
    __tablename__ = "ftp_logs"

    id = Column(BigInteger, Identity(), primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    log_time = Column(DateTime(timezone=True), nullable=False, primary_key=True)
    client_ip = Column(String(45))
    username = Column(String(255))
    action = Column(String(20), nullable=False)
    file_path = Column(Text)
    file_size = Column(BigInteger, default=0)
    transfer_time = Column(Float, default=0)
    transfer_type = Column(String(10))
    status = Column(String(10), default="success")
    session_id = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=_now)

    device = relationship("Device", back_populates="logs")

    __table_args__ = (
        Index("idx_ftp_logs_log_time", "log_time"),
        Index("idx_ftp_logs_device_time", "device_id", "log_time"),
        Index(
            "idx_ftp_logs_username_trgm",
            "username",
            postgresql_using="gin",
            postgresql_ops={"username": "gin_trgm_ops"},
        ),
        {"postgresql_partition_by": "RANGE (log_time)"},
    )


class ServiceMetric(Base):
    """장비 × 시간버킷 서비스 품질 집계 (롤업 잡이 채움)."""
    __tablename__ = "service_metrics"

    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    bucket = Column(DateTime(timezone=True), primary_key=True)
    transfers = Column(Integer, nullable=False, default=0)
    transfer_fails = Column(Integer, nullable=False, default=0)
    bytes = Column(BigInteger, nullable=False, default=0)
    transfer_secs = Column(Float, nullable=False, default=0)
    login_attempts = Column(Integer, nullable=False, default=0)
    login_fails = Column(Integer, nullable=False, default=0)
    cwd_fails = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (
        Index("idx_service_metrics_bucket", "bucket"),
    )


class ServiceAlert(Base):
    """baseline 이탈로 판정된 서비스 이상 이벤트."""
    __tablename__ = "service_alerts"

    id = Column(BigInteger, Identity(), primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    bucket = Column(DateTime(timezone=True), nullable=False)
    metric = Column(String(30), nullable=False)        # fail_rate | throughput | login_fail_rate
    severity = Column(String(10), nullable=False, default="warning")
    value = Column(Float, nullable=False)
    baseline = Column(Float)
    threshold = Column(Float)
    sample_count = Column(Integer)
    message = Column(Text)
    notified = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    device = relationship("Device")

    __table_args__ = (
        UniqueConstraint("device_id", "bucket", "metric", name="uq_service_alert_bucket_metric"),
        Index("idx_service_alerts_created", "created_at"),
    )
