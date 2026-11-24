from datetime import date, datetime
from typing import List, Literal, Optional
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

class FtpAccountMap(BaseModel):
    """고객 계정이 볼 수 있는 (그룹, FTP 아이디) 묶음. 그룹 여러 개 · 아이디 여러 개."""
    group_id: int
    group_name: Optional[str] = None       # 응답에서만 채운다 (화면 표시용)
    usernames: List[str] = Field(default_factory=list)


class PasswordChange(BaseModel):
    """본인 비밀번호 변경 (상단바 '내 계정'). 현재 비밀번호를 함께 받는다."""
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    # 관리자 계정은 customer 가 없다 — 고객 계정일 때만 필수(라우터에서 검사)
    role: str = Field(default="customer", pattern="^(admin|customer)$")
    customer: Optional[str] = None
    allowed_ips: List[str] = []
    note: Optional[str] = Field(default=None, max_length=2000)   # 비고 (담당자 등)
    # 고객 계정이 볼 FTP 계정 매핑. 비우면 그 계정은 아무것도 보지 못한다.
    ftp_accounts: List[FtpAccountMap] = Field(default_factory=list)

class UserUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    customer: Optional[str] = None
    allowed_ips: Optional[List[str]] = None
    is_active: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    ftp_accounts: Optional[List[FtpAccountMap]] = None   # 주면 통째로 교체

class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    customer: Optional[str] = None
    allowed_ips: List[str] = []
    is_active: bool
    ftp_accounts: List[FtpAccountMap] = Field(default_factory=list)
    note: Optional[str] = None
    created_by: Optional[str] = None       # 등록한 관리자 (예전 계정은 비어 있다)
    locked_seconds: int = 0                # 0 이면 잠기지 않음
    last_login_at: Optional[datetime] = None
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
    api_auth_url: Optional[str] = None
    api_purge_url: Optional[str] = None

    class Config:
        from_attributes = True


class TelcoApiUpdate(BaseModel):
    """텔코 CDN API 주소. 계정·비밀번호는 그룹에서 넣는다."""
    api_auth_url: Optional[str] = Field(default=None, max_length=1000)
    api_purge_url: Optional[str] = Field(default=None, max_length=1000)


class TokenTestRequest(BaseModel):
    """그룹 모달의 업로드 ID 행에서 저장 전에 시험 — 입력 중인 값으로 발급해 본다.
    api_password 를 비우면 그 행(target_id), 없으면 group_id 그룹에서 같은 API 계정에 저장된 비밀번호를 쓴다."""
    telco: str = Field(min_length=1, max_length=100)
    api_username: str = Field(min_length=1, max_length=255)
    api_password: Optional[str] = Field(default=None, max_length=1000)
    group_id: Optional[int] = None
    target_id: Optional[int] = None


class TokenTestResult(BaseModel):
    ok: bool
    message: str

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
    # 파티션은 보존 기간만큼 '미리' 만들어 두므로 목록에 있다고 데이터가 있는 건 아니다.
    # 아래 두 값으로 실제 데이터 유무·기간을 구분한다 (없으면 빈 파티션).
    has_rows: bool = False
    first_log: Optional[datetime] = None
    last_log: Optional[datetime] = None

class StorageInfo(BaseModel):
    db_bytes: int                          # 데이터베이스 전체 크기
    ftp_logs_bytes: int                    # ftp_logs 전체(모든 파티션)
    partitions: List[StoragePartition]
    default_rows: int                      # ftp_logs_default 정확한 행 수
    default_months: List[str]              # default 에 들어있는 월(YYYY-MM) 목록
    retention_months: int
    disk_path: str = "/"                   # 사용률을 보는 경로 (PGDATA 를 옮겼으면 그 마운트)
    disk_total_bytes: int = 0              # 위 경로가 속한 디스크
    disk_used_bytes: int = 0
    disk_percent: float = 0.0
    autopurge_enabled: bool = True         # 임계치 초과 시 오래된 월부터 자동 삭제
    autopurge_percent: int = 90

class RebalanceStatus(BaseModel):
    """default 파티션 재배치 작업 상태 (설정 > DB 저장소 버튼)."""
    running: bool = False
    ok: Optional[bool] = None              # 마지막 실행 성공 여부 (아직 돈 적 없으면 None)
    message: str = ""
    notices: List[str] = []                # DB 가 남긴 진행 메시지
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class DiskPurgeUpdate(BaseModel):
    enabled: bool = True
    percent: int = Field(default=90, ge=50, le=99)

class DiskPathUpdate(BaseModel):
    # 존재하는 디렉토리인지는 서버(disk_guard.save_path)가 확인한다
    path: str = Field(default="/", max_length=255)

class RetentionUpdate(BaseModel):
    # app/retention.py 의 허용 범위와 같은 값 (여기서 먼저 걸러 422 로 돌려준다)
    months: int = Field(ge=1, le=120)


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
    # device_key 는 담지 않는다 — 데몬의 인제스트 자격증명이라
    # 알면 누구든 위조 로그를 넣을 수 있다. 목록은 고객 계정·조회용 API 키도 부르므로
    # 화면이 쓰지도 않는 값을 응답에 실으면 그대로 유출된다.
    status: str
    os_info: Optional[str]
    kernel_version: Optional[str] = None
    proftpd_version: Optional[str]
    daemon_version: Optional[str]
    daemon_outdated: bool = False   # 배포된 저장소의 DAEMON_VERSION 과 다른가
    last_heartbeat: Optional[datetime]
    # 데몬 상태
    daemon_status: Optional[str] = "unknown"
    # 하트비트까지 본 상태 — 120초 넘게 소식이 없으면 stopped(종료됨)/lost(응답 없음).
    # 화면은 이 값을 쓴다(규칙을 프론트에 두 벌 두지 않는다).
    effective_daemon_status: str = "unknown"
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

class DaemonUpdateResult(BaseModel):
    """데몬 업데이트 요청 결과 — 장비 단건·다건·그룹이 같은 응답을 쓴다."""
    requested: int = 0            # 플래그를 세운 장비 수


class DaemonUpdateDevices(BaseModel):
    """체크한 장비들을 한꺼번에 업데이트."""
    device_ids: List[int] = []


# ── 로그 재전송(backfill) ── app/backfill.py ────────────────────────────────────

class BackfillRequest(BaseModel):
    date_from: date
    date_to: date
    # 설정된 로그 폴더 안의 파일 이름 패턴(예: ExtendedAllLog.*.gz). None/빈 값 = 그 로그는 읽지 않음
    transfer_pattern: Optional[str] = "TransferLog*"
    extended_pattern: Optional[str] = "ExtendedAllLog*"


class BackfillRequestResult(BaseModel):
    queued: int = 0       # 작업을 만든 장비 수
    skipped: int = 0      # 대상이 아니라 건너뛴 장비 수 (사유는 진행 상황 목록에)


class BackfillJobResponse(BaseModel):
    id: int
    device_id: int
    hostname: Optional[str] = None
    date_from: date
    date_to: date
    status: str
    sent_count: int = 0
    transfer_pattern: Optional[str] = None
    extended_pattern: Optional[str] = None
    message: Optional[str] = None
    requested_by: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    updated_at: datetime
    finished_at: Optional[datetime] = None


class BackfillProgress(BaseModel):
    """장비의 bulk 프로세스가 보내는 진행 보고 (device_key 로 인증)."""
    device_key: str
    job_id: int
    state: Literal["running", "done", "failed"]
    sent: int = 0
    message: Optional[str] = Field(default=None, max_length=2000)


class DeviceConfirm(BaseModel):
    status: str = Field(default="confirmed", pattern="^(confirmed|disabled|pending)$")

class DeviceGroupAssign(BaseModel):
    group_ids: List[int]


# ── Group ─────────────────────────────────────────────────────────────────────

class PurgeTargetIn(BaseModel):
    # 기존 행이면 그 id — 마운트 경로를 바꿔도 같은 행으로 고쳐 비밀번호·토큰을 잃지 않는다
    id: Optional[int] = None
    enabled: bool = True
    ftp_username: str = Field(min_length=1, max_length=255)
    service: str = Field(min_length=1, max_length=255)
    volume: str = Field(min_length=1, max_length=255)
    strip_prefix: Optional[str] = Field(default=None, max_length=1000)
    api_username: Optional[str] = Field(default=None, max_length=255)
    # 비우면 그 행, 없으면 같은 API 계정에 저장돼 있던 비밀번호를 쓴다 (화면은 비밀번호를 다시 보여주지 않는다)
    api_password: Optional[str] = Field(default=None, max_length=1000)


class PurgeTargetOut(BaseModel):
    """비밀번호·토큰 값은 싣지 않는다 — 있는지와 발급 시각만."""
    id: int
    enabled: bool = True
    ftp_username: str
    service: str
    volume: str
    strip_prefix: Optional[str] = None
    api_username: Optional[str] = None
    has_api_password: bool = False
    api_token_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    telco: Optional[str] = Field(default=None, max_length=100)
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None
    purge_active: bool = True
    purge_targets: Optional[List[PurgeTargetIn]] = None   # 주면 전체 교체

class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    telco: Optional[str] = Field(default=None, max_length=100)
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None
    purge_active: Optional[bool] = None
    purge_targets: Optional[List[PurgeTargetIn]] = None   # 주면 전체 교체

class GroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    telco: Optional[str] = None
    customer: Optional[str] = None
    upload_domains: Optional[str] = None
    application: Optional[str] = None
    purge_active: bool = True
    created_at: datetime
    device_count: int = 0
    purge_targets: List[PurgeTargetOut] = []

    class Config:
        from_attributes = True


# ── Ingest ────────────────────────────────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    device_key: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    # 데몬이 시작할 때 한 번 싣는다 — 등록(register)이 실패해도 버전이 갱신되게
    daemon_version: Optional[str] = None
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
    # 과거 로그 재전송분 — 업로드 자동 purge 를 걸지 않는다(몇 주 전 업로드를 지금 purge 하지 않게)
    backfill: bool = False

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
    status: str                       # ok | warning | critical
    daemon_status: Optional[str] = None   # stopped | lost 면 데몬이 죽어 있다 (app/device_health)
    last_bucket: Optional[datetime] = None
    fail_rate: Optional[float] = None
    throughput_mb: Optional[float] = None        # MB/s
    login_fail_rate: Optional[float] = None
    open_alerts: int = 0               # 진행 중인 이상(에피소드) 수 — 복구되면 0
    open_metrics: List[str] = []       # 진행 중인 지표 (fail_rate 등) — 최근 이상 순

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
    ongoing: bool = False      # 같은 장비·지표의 이상이 지금도 이어지는가 (False = 복구됨)

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
    transfer_fails_ignored: int = 0   # 설정의 제외 계정으로 빠진 전송 실패 건수 (transfer_fails 에는 없다)
    login_fails_ignored: int = 0      # 설정의 제외 계정으로 빠진 로그인 실패 건수 (login_fails 에는 없다)


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


class LoginConfigUpdate(BaseModel):
    """로그인 화면 문구·색상. 길이/형식 검증은 app/login_config.py 가 한 곳에서 한다."""
    title: str = ""
    subtitle: str = ""
    warning: str = ""
    bg_color: str = ""


class LoginConfig(LoginConfigUpdate):
    """로그인 전에도 내려가는 값 — 화면에 어차피 보이는 장식만 담는다."""
    bg_image: str = ""             # data URI (비어 있으면 색상 배경)
    logo_image: str = ""           # data URI (비어 있으면 기본 아이콘)


class AlertSettings(BaseModel):
    """이상 감지 임계값. 저장하면 다음 판정 주기(기본 5분)에 반영된다."""
    mad_k: Optional[float] = Field(default=None, ge=1, le=20)
    fail_rate_floor: Optional[float] = Field(default=None, ge=0, le=1)
    login_fail_rate_floor: Optional[float] = Field(default=None, ge=0, le=1)
    throughput_drop: Optional[float] = Field(default=None, ge=0, le=1)
    # baseline 하위 백분위 — 평소에도 이만큼 느린 구간이 있으면 알리지 않는다
    throughput_slow_pct: Optional[float] = Field(default=None, ge=0, le=0.5)
    cwd_fail_floor: Optional[int] = Field(default=None, ge=0)
    # CWD 실패 집계 제외 경로 (한 줄에 하나, '*' 와일드카드)
    cwd_ignore_paths: Optional[str] = Field(default=None, max_length=2000)
    # 전송 실패 집계 제외 FTP 계정 (한 줄에 하나, '*' 와일드카드)
    xfer_ignore_accounts: Optional[str] = Field(default=None, max_length=2000)
    # 로그인 실패율 판정 제외 FTP 계정 (한 줄에 하나, '*' 와일드카드)
    login_ignore_accounts: Optional[str] = Field(default=None, max_length=2000)
    # 발송 전 이상이 이어져야 하는 버킷 수 (1 = 즉시 발송)
    min_streak_buckets: Optional[int] = Field(default=None, ge=1, le=12)
    min_streak_throughput: Optional[int] = Field(default=None, ge=1, le=12)
    # 하트비트 끊김을 몇 번(판정 주기마다) 연달아 확인한 뒤 알릴지 — 짧은 재시작 오탐 방지
    min_streak_heartbeat: Optional[int] = Field(default=None, ge=1, le=12)
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


# ── Purge ─────────────────────────────────────────────────────────────────────

class PurgeJobItem(BaseModel):
    id: int
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    telco: Optional[str] = None
    ftp_username: Optional[str] = None
    file_path: str
    purge_path: Optional[str] = None
    service: Optional[str] = None
    volume: Optional[str] = None
    status: str
    attempts: int
    last_error: Optional[str] = None
    created_at: datetime
    done_at: Optional[datetime] = None


class PurgeRetryResult(BaseModel):
    requeued: int = 0
