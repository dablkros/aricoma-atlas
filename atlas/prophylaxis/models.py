"""Secret-free normalized results for Profylaxia checks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CPU_CHECK_NAME = "CPU utilization"


class CPUUtilizationValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_percent: float = Field(ge=0, le=100)
    one_minute_percent: float | None = Field(default=None, ge=0, le=100)
    five_minute_percent: float | None = Field(default=None, ge=0, le=100)


class CPUCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: str
    platform: str | None = None
    check: Literal["CPU utilization"] = CPU_CHECK_NAME
    status: Literal["ok", "error"]
    values: CPUUtilizationValues | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self):
        if self.status == "ok" and (self.values is None or self.error is not None):
            raise ValueError("successful result requires values and no error")
        if self.status == "error" and (self.error is None or self.values is not None):
            raise ValueError("error result requires an error and no values")
        return self


__all__ = ["CPU_CHECK_NAME", "CPUCheckResult", "CPUUtilizationValues"]
