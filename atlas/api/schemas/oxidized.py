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
