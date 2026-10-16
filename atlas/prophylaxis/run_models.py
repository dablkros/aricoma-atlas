"""Secret-free contracts for asynchronous Profylaxia runs."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.prophylaxis.models import (
    CPUUtilizationValues,
    DeviceFactsValues,
    OSVersionValues,
    UptimeValues,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProphylaxisCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str = Field(default="device_facts", min_length=1, max_length=128)
    device_id: int | None = Field(default=None, gt=0)
    device_name: str = Field(min_length=1, max_length=255)
    management_ip: str | None = None
    platform: str | None = None
    status: Literal["success", "failed", "incomplete", "skipped"]
    facts: DeviceFactsValues | None = None
    values: CPUUtilizationValues | OSVersionValues | UptimeValues | None = None
    error: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "success":
            payloads = int(self.facts is not None) + int(self.values is not None)
            if payloads != 1 or self.error is not None:
                raise ValueError("successful result requires one payload")
        elif self.facts is not None or self.values is not None or not self.error:
            raise ValueError("non-success result requires only a safe error code")
        return self


# Compatibility alias for callers importing the previous result name.
DeviceFactsResult = ProphylaxisCheckResult


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(default=0, ge=0)
    success: int = Field(default=0, ge=0)
    failure: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)


class ProphylaxisRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    operation: Literal["selected_checks", "device_facts"] = "selected_checks"
    status: Literal["PENDING", "RUNNING", "COMPLETED", "PARTIAL", "FAILED"]
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: RunSummary = Field(default_factory=RunSummary)
    results: tuple[ProphylaxisCheckResult, ...] = ()
    report_path: str
    error: str | None = Field(default=None, max_length=128)


__all__ = [
    "DeviceFactsResult",
    "ProphylaxisCheckResult",
    "ProphylaxisRun",
    "RunSummary",
    "utc_now",
]
