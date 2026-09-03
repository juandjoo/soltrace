from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = "admin"
    customer: Optional[str] = None


# ── 사용자(고객 계정) 관리 ──────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    customer: str = Field(min_length=1)
    allowed_ips: List[str] = []

class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    customer: Optional[str] = None
    allowed_ips: Optional[List[str]] = None
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    customer: Optional[str] = None
    allowed_ips: List[str] = []
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ── API 키 (조회 전용 토큰) ────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    # None = 관리자(admin) 키, 값이 있으면 해당 고객 계정의 키
    user_id: Optional[int] = None
    label: str = Field(default="", max_length=100)
    expires_at: Optional[datetime] = None

class ApiKeyResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: str                  # 소유 계정 (관리자 키면 관리자 아이디)
    role: str                      # admin | customer
    customer: Optional[str] = None
    label: str = ""
    key_prefix: str
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

class ApiKeyCreated(ApiKeyResponse):
    # 발급 직후 1회만 내려주는 평문 키 (저장하지 않으므로 다시 볼 수 없다)
    key: str


# ── Settings ──────────────────────────────────────────────────────────────────

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

class TelcoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

class TelcoItem(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class VersionInfo(BaseModel):
    branch: Optional[str] = None
    commit: Optional[str] = None
    commit_date: Optional[str] = None
    subject: Optional[str] = None
    behind: Optional[int] = None          # 원격 대비 뒤처진 커밋 수 (확인 시)
    update_available: bool = False
    checked: bool = False                 # 원격 fetch 수행 여부
    error: Optional[str] = None           # 원격 확인 실패 사유

class UpdateTriggerResponse(BaseModel):
    started: bool
    message: str

class ChangelogItem(BaseModel):
    text: str
    commit: Optional[str] = None


class ChangelogEntry(BaseModel):
    """changelog.md 의 날짜 한 덩어리."""
    date: str
    items: List[ChangelogItem] = []


class StoragePartition(BaseModel):
    name: str
    rows_est: int                          # pg_class.reltuples (통계 추정치)
    total_bytes: int                       # 테이블 + 인덱스 + TOAST
    table_bytes: int
    index_bytes: int

class StorageInfo(BaseModel):
    db_bytes: int                          # 데이터베이스 전체 크기
    ftp_logs_bytes: int                    # ftp_logs 전체(모든 파티션)
    partitions: List[StoragePartition]
    default_rows: int                      # ftp_logs_default 정확한 행 수
    default_months: List[str]              # default 에 들어있는 월(YYYY-MM) 목록
    retention_months: int


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceBase(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    os_info: Optional[str] = None
    kernel_version: Optional[str] = None
    proftpd_version: Optional[str] = None
    daemon_version: Optional[str] = None

class DeviceRegister(DeviceBase):
    device_key: str

class GroupBrief(BaseModel):
    id: int
    name: str
    telco: Optional[str] = None

    class Config:
        from_attributes = True

class DeviceResponse(BaseModel):
    id: int
    hostname: str
    ip_address: Optional[str]
    device_key: str
    status: str
    os_info: Optional[str]
    kernel_version: Optional[str] = None
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
    update_requested: bool = False
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
    description: Optional[str] = None
    telco: Optional[str] = Field(default=None, max_length=100)
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None

class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    telco: Optional[str] = Field(default=None, max_length=100)
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None

class GroupDeviceAssign(BaseModel):
    device_ids: List[int]

class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    telco: Optional[str] = None
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None
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
    device_ip: Optional[str] = None
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

class LogListResponse(BaseModel):
    # 총건수는 별도 엔드포인트(/logs/count)에서 정확히 센다 — 목록 응답을 COUNT 가 막지 않게
    total: Optional[int] = None
    page: int
    size: int
    items: List[FtpLogResponse]

class LogCountResponse(BaseModel):
    total: int


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
    customer: Optional[str] = None

class HourlyPoint(BaseModel):
    bucket: str  # "2026-06-18T14:00:00Z"
    uploads: int
    downloads: int
    bytes_in: int
    bytes_out: int
    # 사용자별 차트(업로드/삭제)용 — 그룹별 시리즈에서는 채우지 않는다
    deletes: int = 0
    bytes_del: int = 0

class GroupHourlySeries(BaseModel):
    group_id: int
    name: str
    telco: Optional[str] = None
    data: List[HourlyPoint]

class UserHourlySeries(BaseModel):
    username: str
    data: List[HourlyPoint]

class DashboardDetail(BaseModel):
    stats: DashboardStats
    timeseries: List[TimeSeriesPoint]
    top_users: List[TopItem]
    top_devices: List[TopItem]
    top_groups: List[TopItem] = []
    by_action: dict


# ── Service health (서비스 영향도) ──────────────────────────────────────────────

class ServiceHealthDevice(BaseModel):
    device_id: int
    hostname: str
    status: str                       # ok | warning | critical | idle
    last_bucket: Optional[datetime] = None
    fail_rate: Optional[float] = None
    throughput_mb: Optional[float] = None        # MB/s
    login_fail_rate: Optional[float] = None
    open_alerts: int = 0

class ServiceAlertItem(BaseModel):
    id: int
    device_id: int
    hostname: str
    bucket: datetime
    metric: str
    severity: str
    value: float
    baseline: Optional[float] = None
    message: Optional[str] = None
    created_at: datetime

class ServiceTrendPoint(BaseModel):
    bucket: datetime
    fail_rate: Optional[float] = None
    throughput_mb: Optional[float] = None
    login_fail_rate: Optional[float] = None
    cwd_fails: Optional[int] = None

class FailTotals(BaseModel):
    transfer_fails: int = 0
    login_fails: int = 0
    cwd_fails: int = 0            # 진짜 이동 실패만 (알림/추이와 같은 기준)
    cwd_fails_ignored: int = 0    # 그중 설정의 제외 경로로 빠진 건수 (존재 확인 건은 아예 세지 않는다)


class CwdFailPath(BaseModel):
    file_path: Optional[str] = None
    count: int = 0
    users: int = 0
    ips: int = 0
    last_seen: Optional[datetime] = None
    ignored: bool = False         # 현재 제외 경로 설정에 걸리는 경로


class CwdFailUser(BaseModel):
    username: Optional[str] = None
    count: int = 0
    paths: int = 0


class CwdFailBreakdown(BaseModel):
    total: int = 0                # 기간 내 cwd_fail 전체 (제외 경로·존재 확인 포함)
    ignored: int = 0              # 그중 제외 경로에 걸린 건수
    probes: int = 0               # 그중 존재 확인(실패 직후 그 경로가 생성됨)으로 걸러낸 건수
    distinct_paths: int = 0
    top_share: float = 0.0        # 상위 경로 3개가 전체에서 차지하는 비율
    paths: List[CwdFailPath] = []
    users: List[CwdFailUser] = []
    ignore_patterns: List[str] = []   # 관리자에게만 채워 보낸다

class NotifySettings(BaseModel):
    webhook_url: str = ""
    hms_url: str = ""              # HMS 메일 게이트웨이 URL


class AlertSettings(BaseModel):
    """이상 감지 임계값. 저장하면 다음 판정 주기(기본 5분)에 반영된다."""
    mad_k: Optional[float] = Field(default=None, ge=1, le=20)
    fail_rate_floor: Optional[float] = Field(default=None, ge=0, le=1)
    login_fail_rate_floor: Optional[float] = Field(default=None, ge=0, le=1)
    throughput_drop: Optional[float] = Field(default=None, ge=0, le=1)
    cwd_fail_floor: Optional[int] = Field(default=None, ge=0)
    # CWD 실패 집계 제외 경로 (한 줄에 하나, '*' 와일드카드)
    cwd_ignore_paths: Optional[str] = Field(default=None, max_length=2000)
    min_samples: Optional[int] = Field(default=None, ge=1)
    min_login_samples: Optional[int] = Field(default=None, ge=1)
    min_cwd_samples: Optional[int] = Field(default=None, ge=1)
    min_large_samples: Optional[int] = Field(default=None, ge=1)


class AlertSettingsInfo(AlertSettings):
    # 참고용 (읽기 전용) — 롤업 시점에 반영되는 값이라 화면에서 바꾸지 않는다
    large_file_bytes: int = 0
    bucket_minutes: int = 0
    baseline_days: int = 0


class ServiceHealthResponse(BaseModel):
    devices: List[ServiceHealthDevice]
    alerts: List[ServiceAlertItem]
    trend: List[ServiceTrendPoint] = []
    fail_totals: FailTotals = FailTotals()
