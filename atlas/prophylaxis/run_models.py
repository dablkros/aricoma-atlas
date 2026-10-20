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
    EvaluationOutcome,
    SystemMetricValues,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SerialInventoryValues(BaseModel):
    """Result of comparing the collected chassis serial with NetBox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    state: Literal["populated", "matched", "mismatch"]
    collected_serial: str = Field(min_length=1, max_length=255)
    netbox_serial: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_outcome(self):
        expected = (
            EvaluationOutcome.FAIL
            if self.state == "mismatch"
            else EvaluationOutcome.PASS
        )
        if self.outcome != expected:
            raise ValueError("serial inventory state does not match outcome")
        return self


class ModelInventoryValues(BaseModel):
    """Result of comparing the collected hardware model with NetBox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    state: Literal["matched", "mismatch"]
    collected_model: str = Field(min_length=1, max_length=255)
    netbox_model: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_outcome(self):
        expected = (
            EvaluationOutcome.FAIL
            if self.state == "mismatch"
            else EvaluationOutcome.PASS
        )
        if self.outcome != expected:
            raise ValueError("model inventory state does not match outcome")
        return self


class ProphylaxisCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str = Field(default="device_facts", min_length=1, max_length=128)
    device_id: int | None = Field(default=None, gt=0)
    device_name: str = Field(min_length=1, max_length=255)
    management_ip: str | None = None
    platform: str | None = None
    status: Literal["success", "failed", "incomplete", "skipped"]
    evaluation: EvaluationOutcome
    facts: DeviceFactsValues | None = None
    values: (
        CPUUtilizationValues
        | OSVersionValues
        | UptimeValues
        | SystemMetricValues
        | SerialInventoryValues
        | ModelInventoryValues
        | None
    ) = None
    error: str | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_evaluation(cls, value):
        if isinstance(value, dict) and "evaluation" not in value:
            status = value.get("status")
            inferred = {
                "success": EvaluationOutcome.PASS,
                "failed": EvaluationOutcome.FAIL,
                "incomplete": EvaluationOutcome.UNKNOWN,
                "skipped": EvaluationOutcome.UNSUPPORTED,
            }.get(status)
            if inferred is not None:
                value = {**value, "evaluation": inferred}
        return value

    @model_validator(mode="after")
    def validate_payload(self):
        expected_status = {
            EvaluationOutcome.PASS: "success",
            EvaluationOutcome.FAIL: "failed",
            EvaluationOutcome.UNKNOWN: "incomplete",
            EvaluationOutcome.NOT_APPLICABLE: "skipped",
            EvaluationOutcome.UNSUPPORTED: "skipped",
        }[self.evaluation]
        if self.status != expected_status:
            raise ValueError("status does not match evaluation")
        if self.status == "success":
            payloads = int(self.facts is not None) + int(self.values is not None)
            if payloads != 1 or self.error is not None:
                raise ValueError("successful result requires one payload")
        elif self.facts is not None:
            raise ValueError("non-success result cannot contain facts")
        elif self.values is None and not self.error:
            raise ValueError("non-success result requires values or a safe error code")
        elif self.values is not None and self.error is not None:
            raise ValueError("evaluated result must not also contain an error")
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
    "ModelInventoryValues",
    "ProphylaxisCheckResult",
    "ProphylaxisRun",
    "RunSummary",
    "SerialInventoryValues",
    "utc_now",
]
