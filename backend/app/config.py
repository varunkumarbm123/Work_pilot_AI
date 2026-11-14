from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    database_url: str
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    openai_embedding_model: str = ""
    frontend_origins: str = ""
    connector_adapter: str = "sample"
    smtp_host: str = ""
    smtp_port: int | None = None
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    seed_on_startup: bool = True
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
