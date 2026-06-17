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
    alert_min_login_samples: int = 10      # 로그인 지표 평가 최소 시도수
    alert_min_cwd_samples: int = 5         # CWD fail 평가 최소 건수
    alert_fail_rate_floor: float = 0.05    # 전송 실패율 절대 하한 (5%)
    alert_login_fail_rate_floor: float = 0.30   # 로그인 실패율 절대 하한 (30%)
    alert_cwd_fail_floor: int = 20         # CWD fail 절대 하한 (건수 기준, 소량 정상탐색 무시)
    alert_throughput_drop: float = 0.5     # baseline 대비 throughput 하락 비율 (50%↓)

    # 알림 채널 (미설정 시 해당 채널 비활성)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    alert_email_to: str = ""               # 쉼표 구분 수신자
    alert_webhook_url: str = ""            # POST(JSON) 발송 대상
    alert_hms_url: str = ""               # HMS 메일 게이트웨이 URL

    @property
    def alert_email_recipients(self) -> list[str]:
        return [a.strip() for a in self.alert_email_to.split(",") if a.strip()]

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
