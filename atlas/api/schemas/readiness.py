"""Readiness endpoint response schema."""

from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.services.errors import DependencyReason


class DependencyStatuses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openbao: Literal["ok", "error"]
    netbox: Literal["ok", "error"]
    oxidized: Literal["ok", "error"]
    zabbix: Literal["ok", "error"]


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    dependencies: DependencyStatuses
    reasons: Dict[str, DependencyReason] = Field(default_factory=dict)
