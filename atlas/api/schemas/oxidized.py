"""Public, secret-free schemas for Oxidized inventory synchronization."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class OxidizedSyncIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: Optional[str]
    code: str


class OxidizedSyncSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    added: int
    updated: int
    removed: int
    unchanged: int
    skipped: int
    errors: int


class OxidizedSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "partial_success", "error"]
    inventory_changed: bool
    inventory_updated: bool
    oxidized_reloaded: bool
    summary: OxidizedSyncSummary
    added: List[str]
    updated: List[str]
    removed: List[str]
    issues: List[OxidizedSyncIssue]


class OxidizedStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "degraded", "unhealthy"]
    netbox_enabled_devices: int
    runtime_inventory_devices: int
    inventory_issues: int


class OxidizedDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ip: str
    model: str


class OxidizedBackupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    device: str
