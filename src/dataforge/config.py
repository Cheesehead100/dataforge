"""Runtime settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr
    parse_model: str = "claude-haiku-4-5-20251001"
    generate_model: str = "claude-sonnet-4-6"
    output_dir: Path = Path("./output")
    max_tokens_parse: int = 2048
    max_tokens_generate: int = 8192


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
