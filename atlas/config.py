"""Validated application settings loaded from environment variables."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
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
    openbao_url: AnyHttpUrl = "http://127.0.0.1:18200"
    openbao_identity_file: Path = Path(".runtime/openbao-backend.json")
    netbox_url: AnyHttpUrl = "http://127.0.0.1:8000"
    oxidized_url: AnyHttpUrl = "http://127.0.0.1:8888"
    zabbix_url: AnyHttpUrl = "http://127.0.0.1:8082/api_jsonrpc.php"
    oxidized_inventory_file: Path = Path("/run/atlas/oxidized/router.json")
    prophylaxis_results_file: Path = Path(
        "/run/atlas/prophylaxis/results.sqlite3"
    )
    prophylaxis_result_retention: int = Field(
        default=10_000,
        ge=100,
        le=1_000_000,
    )
    ansible_project_dir: Path = Path("automation")
    ansible_job_timeout: int = Field(default=120, ge=1, le=1800)
    ssh_known_hosts_file: Path = Path("/run/atlas/ssh/known_hosts")
    ssh_strict_host_keys: bool = False
    ssh_connect_timeout: int = Field(default=10, ge=1, le=60)
    ssh_command_timeout: int = Field(default=20, ge=1, le=300)
    fortios_validate_certs: bool = True
    http_connect_timeout: float = Field(default=3.0, gt=0, le=30)
    http_read_timeout: float = Field(default=10.0, gt=0, le=120)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        validate_default=True,
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

    @field_validator("openbao_url", "netbox_url", "oxidized_url", "zabbix_url")
    @classmethod
    def validate_dependency_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError(
                "dependency URLs must not contain credentials, query, or fragment"
            )
        return value

    @field_validator(
        "openbao_identity_file",
        "oxidized_inventory_file",
        "prophylaxis_results_file",
        "ansible_project_dir",
        "ssh_known_hosts_file",
    )
    @classmethod
    def validate_runtime_file(cls, value: Path) -> Path:
        if not value.name or ".." in value.parts:
            raise ValueError("runtime path must identify a file")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable settings instance per process."""

    return Settings()
