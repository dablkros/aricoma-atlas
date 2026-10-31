"""Validated contracts for browser-driven inventory imports."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InventoryTableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(min_length=1, max_length=2_000_000)
    tenant: str | None = Field(default=None, max_length=200)
    site: str | None = Field(default=None, max_length=200)
    platform_overrides: dict[str, str] = Field(default_factory=dict, max_length=100)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("platform_overrides")
    @classmethod
    def validate_platform_overrides(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for row, platform in values.items():
            row_number = row.strip()
            platform_slug = platform.strip()
            if not row_number.isdigit() or int(row_number) < 2:
                raise ValueError("platform override row is invalid")
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", platform_slug):
                raise ValueError("platform override slug is invalid")
            normalized[row_number] = platform_slug
        return normalized


class DeviceTypeUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.ya?ml$")
    yaml_content: str = Field(min_length=1, max_length=1_000_000)
    replace: bool = False


class TenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(
        default=None, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    group: str | None = Field(default=None, min_length=1, max_length=100)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class SiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


__all__ = [
    "InventoryTableRequest", "DeviceTypeUploadRequest", "SiteRequest", "TenantRequest",
]
