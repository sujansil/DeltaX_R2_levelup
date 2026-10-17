from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    secret_key: str
    access_token_expire_hours: int = 16
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    admin_username: str = "admin"
    admin_password: str
    initial_team_score: int = 0
    cors_origins: str = "http://localhost:8000"

    topic_duration_sec: int = 45
    kingslayer_after_question: int = 5
    enable_bounty: bool = True
    enable_freeze: bool = True
    enable_peek: bool = True
    enable_swap: bool = True
    enable_kingslayer: bool = True
    controller_ttl_sec: int = 40
    jwt_algorithm: str = "HS256"


@lru_cache
def get_settings() -> Settings:
    return Settings()
