from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Column, DateTime, Float, ForeignKey,
    Index, Integer, String, Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45))
    device_key = Column(String(64), unique=True, nullable=False)
    status = Column(String(20), default="pending")
    os_info = Column(Text)
    proftpd_version = Column(String(50))
    daemon_version = Column(String(20))
    last_heartbeat = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    groups = relationship("Group", secondary="device_groups", back_populates="devices")
    logs = relationship("FtpLog", back_populates="device", lazy="dynamic")


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    group_type = Column(String(50), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)

    devices = relationship("Device", secondary="device_groups", back_populates="groups")


class DeviceGroup(Base):
    __tablename__ = "device_groups"

    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)


class FtpLog(Base):
    __tablename__ = "ftp_logs"

    id = Column(BigInteger, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    log_time = Column(DateTime(timezone=True), nullable=False)
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
        Index("idx_ftp_logs_device_time", "device_id", "log_time"),
        Index("idx_ftp_logs_username_time", "username", "log_time"),
    )
