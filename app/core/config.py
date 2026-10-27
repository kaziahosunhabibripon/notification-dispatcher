from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = (
        "postgresql+asyncpg://user:password@localhost:5432/notifications"
    )
    redis_url: str = "redis://localhost:6379/0"
    mock_provider_url: str = "http://localhost:8001"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    claim_batch_size: int = Field(default=100, ge=1, le=1000)
    worker_concurrency: int = Field(default=20, ge=1, le=500)
    lease_duration_seconds: int = Field(default=30, ge=5, le=3600)
    max_attempts: int = Field(default=5, ge=1, le=20)
    provider_timeout_seconds: float = Field(default=5.0, gt=0)
    tenant_rate_limit_per_second: int = Field(default=100, ge=1)
    provider_rate_limit_per_second: int = Field(default=1000, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
