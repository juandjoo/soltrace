from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceBase(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    os_info: Optional[str] = None
    proftpd_version: Optional[str] = None
    daemon_version: Optional[str] = None

class DeviceRegister(DeviceBase):
    device_key: str

class GroupBrief(BaseModel):
    id: int
    name: str
    group_type: str

    class Config:
        from_attributes = True

class DeviceResponse(BaseModel):
    id: int
    hostname: str
    ip_address: Optional[str]
    device_key: str
    status: str
    os_info: Optional[str]
    proftpd_version: Optional[str]
    daemon_version: Optional[str]
    last_heartbeat: Optional[datetime]
    # 데몬 상태
    daemon_status: Optional[str] = "unknown"
    last_send_time: Optional[datetime] = None
    buffer_lines: Optional[int] = 0
    queue_size: Optional[int] = 0
    consecutive_failures: Optional[int] = 0
    error_message: Optional[str] = None
    cpu_percent: Optional[float] = None
    mem_mb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    daemon_uptime: Optional[int] = None
    created_at: datetime
    groups: List[GroupBrief] = []

    class Config:
        from_attributes = True

class DeviceConfirm(BaseModel):
    status: str = Field(default="confirmed", pattern="^(confirmed|disabled|pending)$")

class DeviceGroupAssign(BaseModel):
    group_ids: List[int]


# ── Group ─────────────────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    group_type: str = Field(pattern="^(telco|service|other)$")
    description: Optional[str] = None

class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    group_type: Optional[str] = Field(default=None, pattern="^(telco|service|other)$")
    description: Optional[str] = None

class GroupResponse(BaseModel):
    id: int
    name: str
    group_type: str
    description: Optional[str]
    created_at: datetime
    device_count: int = 0

    class Config:
        from_attributes = True


# ── Ingest ────────────────────────────────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    device_key: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    # 데몬 상태 (running / degraded / error / stopping)
    daemon_status: Optional[str] = None
    last_send_time: Optional[datetime] = None
    buffer_lines: Optional[int] = None
    queue_size: Optional[int] = None
    consecutive_failures: Optional[int] = None
    error_message: Optional[str] = None
    cpu_percent: Optional[float] = None
    mem_mb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    daemon_uptime: Optional[int] = None

class LogEntry(BaseModel):
    log_time: datetime
    client_ip: Optional[str] = None
    username: Optional[str] = None
    action: str
    file_path: Optional[str] = None
    file_size: int = 0
    transfer_time: float = 0.0
    transfer_type: Optional[str] = None
    status: str = "success"
    session_id: Optional[str] = None

class LogBatch(BaseModel):
    device_key: str
    logs: List[LogEntry] = Field(max_length=500)

class IngestResponse(BaseModel):
    accepted: int
    rejected: int = 0


# ── Logs ──────────────────────────────────────────────────────────────────────

class FtpLogResponse(BaseModel):
    id: int
    device_id: int
    device_hostname: Optional[str] = None
    log_time: datetime
    client_ip: Optional[str]
    username: Optional[str]
    action: str
    file_path: Optional[str]
    file_size: int
    transfer_time: float
    transfer_type: Optional[str]
    status: str

    class Config:
        from_attributes = True

class LogQueryParams(BaseModel):
    device_id: Optional[int] = None
    username: Optional[str] = None
    action: Optional[str] = None
    status: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=500)

class LogListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[FtpLogResponse]


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total_uploads: int
    total_downloads: int
    total_deletes: int
    total_bytes_in: int
    total_bytes_out: int
    active_devices: int
    active_users: int
    period_days: int

class TimeSeriesPoint(BaseModel):
    date: str
    uploads: int
    downloads: int
    deletes: int
    bytes_in: int
    bytes_out: int

class TopItem(BaseModel):
    label: str
    count: int
    bytes: int

class DashboardDetail(BaseModel):
    stats: DashboardStats
    timeseries: List[TimeSeriesPoint]
    top_users: List[TopItem]
    top_devices: List[TopItem]
    by_action: dict
