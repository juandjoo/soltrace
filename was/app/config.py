from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://soltrace:soltracepass@localhost:5432/soltrace"
    secret_key: str = "change-this-secret-key-32chars-min"
    admin_password: str = "Admin1234!"
    access_token_expire_minutes: int = 60 * 24  # 24h

    class Config:
        env_file = ".env"


settings = Settings()
