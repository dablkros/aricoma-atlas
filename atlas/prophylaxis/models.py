"""Secret-free execution and normalized Profylaxia result contracts."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = 1
CPU_NETBOX_VALUES = frozenset({"cpu_utilization", "CPU utilization"})


class CheckId(StrEnum):
    CPU_UTILIZATION = "cpu_utilization"
    OS_VERSION = "os_version"
    CPU_TEMPERATURE = "cpu_temperature"
    UPTIME = "uptime"


class CPUUtilizationValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_percent: float = Field(ge=0, le=100)
    one_minute_percent: float | None = Field(default=None, ge=0, le=100)
    five_minute_percent: float | None = Field(default=None, ge=0, le=100)


class RawCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    check: CheckId
    platform: str
    status: Literal["ok", "error", "unsupported"]
    data: Any | None = None
    raw_output: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status == "ok":
            if self.error_code is not None or (
                self.data is None and self.raw_output is None
            ):
                raise ValueError("successful raw result requires data")
        elif self.error_code is None or self.data is not None or self.raw_output is not None:
            raise ValueError("non-success raw result requires only an error code")
        return self


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    device_id: int | None = Field(default=None, gt=0)
    device: str
    platform: str | None = None
    check: CheckId = CheckId.CPU_UTILIZATION
    status: Literal["ok", "error", "unsupported"]
    values: CPUUtilizationValues | None = None
    error_code: str | None = None
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "ok" and (
            self.values is None or self.error_code is not None
        ):
            raise ValueError("successful result requires values and no error")
        if self.status != "ok" and (
            self.error_code is None or self.values is not None
        ):
            raise ValueError("non-success result requires an error and no values")
        return self


class StoredCheckResult(CheckResult):
    id: int = Field(gt=0)


# Compatibility aliases keep the current CPU-only API surface stable while the
# internal contract is ready for additional CheckId values.
CPU_CHECK_NAME = CheckId.CPU_UTILIZATION.value
CPUCheckResult = CheckResult
StoredCPUCheckResult = StoredCheckResult


__all__ = [
    "CPU_CHECK_NAME",
    "CPU_NETBOX_VALUES",
    "CPUCheckResult",
    "CPUUtilizationValues",
    "CheckId",
    "CheckResult",
    "RawCheckResult",
    "SCHEMA_VERSION",
    "StoredCPUCheckResult",
    "StoredCheckResult",
]
