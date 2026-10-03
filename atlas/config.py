"""Validated application settings loaded from environment variables."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas import __version__


class Settings(BaseSettings):
    """Atlas API configuration.

    Environment variables use the ``ATLAS_`` prefix. Local development may
    use a repository-root ``.env`` file, which is excluded from Git.
    """

    app_name: str = Field(default="Aricoma Atlas", min_length=1, max_length=100)
    app_version: str = Field(default=__version__, min_length=1, max_length=50)
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
            raise ValueError(
                "api_prefix must be an absolute path without a trailing slash"
            )
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable settings instance per process."""

    return Settings()
