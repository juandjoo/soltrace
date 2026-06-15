from pydantic import field_validator
from pydantic_settings import BaseSettings

_DEFAULT_SECRET = "change-this-secret-key-32chars-min"
_DEFAULT_ADMIN = "Admin1234!"


class Settings(BaseSettings):
    database_url: str = "postgresql://soltrace:soltracepass@localhost:5432/soltrace"
    secret_key: str = _DEFAULT_SECRET
    admin_password: str = _DEFAULT_ADMIN
    access_token_expire_minutes: int = 60 * 24  # 24h

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
