from pydantic import field_validator
from pydantic_settings import BaseSettings

_DEFAULT_SECRET = "change-this-secret-key-32chars-min"
_DEFAULT_ADMIN = "Admin1234!"


class Settings(BaseSettings):
    database_url: str = "postgresql://soltrace:soltracepass@localhost:5432/soltrace"
    secret_key: str = _DEFAULT_SECRET
    admin_username: str = "admin"
    admin_password: str = _DEFAULT_ADMIN
    access_token_expire_minutes: int = 60        # 1h

    # ── 설정 페이지 (버전/자가 업데이트) ────────────────────────────────
    repo_dir: str = "/opt/soltrace"                          # git 저장소 경로
    selfupdate_cmd: str = "/usr/local/sbin/soltrace-selfupdate"  # sudo 로 실행할 root 래퍼

    # ── 서비스 영향도 감지 ──────────────────────────────────────────────
    alerts_enabled: bool = True
    alert_bucket_minutes: int = 10          # 집계 버킷 크기
    alert_rollup_interval_sec: int = 300    # 롤업/판정 주기 (5분)
    alert_baseline_days: int = 7            # baseline 산출 기간
    alert_mad_k: float = 4.0               # median + k·MAD 이탈 임계
    alert_min_samples: int = 20            # 전송 지표 평가 최소 건수 (소표본 오탐 방지)
    # 전송 속도(throughput) 판정 대상 — 이 크기 이상의 전송만 집계한다.
    # 작은 파일은 연결/인증 오버헤드가 전송시간의 대부분이라 실효속도가 낮게 나오고,
    # 소량 파일 대량 업로드가 성능 저하로 오인된다. 롤업 시점에 반영되는 값이므로
    # 변경 시 baseline(기본 7일)이 새 기준으로 다시 쌓일 때까지 판정이 보수적으로 동작한다.
    alert_large_file_bytes: int = 4 * 1024 * 1024   # 4MB
    alert_min_large_samples: int = 5       # 전송 속도 평가 최소 '큰 파일' 건수
    alert_min_login_samples: int = 10      # 로그인 지표 평가 최소 시도수
    alert_min_cwd_samples: int = 5         # CWD fail 평가 최소 건수
    alert_fail_rate_floor: float = 0.05    # 전송 실패율 절대 하한 (5%)
    alert_login_fail_rate_floor: float = 0.30   # 로그인 실패율 절대 하한 (30%)
    alert_cwd_fail_floor: int = 20         # CWD fail 절대 하한 (건수 기준, 소량 정상탐색 무시)
    # CWD 실패 집계에서 뺄 경로 패턴 (줄 단위, '*' 와일드카드). 홈/업로드 경로 설정 오류처럼
    # 원인이 밝혀진 경로를 알림에서만 제외한다 — 로그 조회에는 그대로 남는다.
    # .env 는 여러 줄 값을 담지 못하므로 설정 페이지에서만 입력한다(여기는 빈 기본값 용도).
    alert_cwd_ignore_paths: str = ""
    alert_throughput_drop: float = 0.5     # baseline 대비 throughput 하락 비율 (50%↓)

    # 알림 채널 (미설정 시 해당 채널 비활성)
    alert_webhook_url: str = ""            # POST(JSON) 발송 대상
    alert_hms_url: str = ""               # HMS 메일 게이트웨이 URL

    @field_validator("secret_key")
    @classmethod
    def secret_key_not_default(cls, v: str) -> str:
        if v == _DEFAULT_SECRET:
            raise ValueError("SECRET_KEY가 기본값입니다. .env에서 변경하세요.")
        return v

    @field_validator("admin_password")
    @classmethod
    def admin_password_not_default(cls, v: str) -> str:
        if v == _DEFAULT_ADMIN:
            raise ValueError("ADMIN_PASSWORD가 기본값입니다. .env에서 변경하세요.")
        return v

    class Config:
        env_file = ".env"


settings = Settings()
