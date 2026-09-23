from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    secret_key: str = "dev-only-not-for-production"
    access_token_expire_hours: int = 16
    database_url: str = "postgresql+asyncpg://levelup:levelup@localhost:5432/levelup"
    redis_url: str = "redis://localhost:6379/0"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    initial_team_score: int = 0
    cors_origins: str = "http://localhost:8000"

    question_duration_sec: int = 150
    freeze_bonus_sec: int = 30
    decay_start_sec: int = 60
    decay_end_sec: int = 135
    kingslayer_after_question: int = 5
    controller_ttl_sec: int = 40
    jwt_algorithm: str = "HS256"


@lru_cache
def get_settings() -> Settings:
    return Settings()
