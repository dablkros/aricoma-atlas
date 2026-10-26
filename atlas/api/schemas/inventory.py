"""Validated contracts for browser-driven inventory imports."""

from pydantic import BaseModel, ConfigDict, Field


class InventoryTableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(min_length=1, max_length=2_000_000)
    tenant: str | None = Field(default=None, max_length=200)
    site: str | None = Field(default=None, max_length=200)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DeviceTypeUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.ya?ml$")
    yaml_content: str = Field(min_length=1, max_length=1_000_000)
    replace: bool = False


__all__ = ["InventoryTableRequest", "DeviceTypeUploadRequest"]
