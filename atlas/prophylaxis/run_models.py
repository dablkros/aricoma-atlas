"""Secret-free contracts for asynchronous Profylaxia runs."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from atlas.prophylaxis.models import (
    CollectionStatus,
    CPUUtilizationValues,
    DeviceFactsValues,
    DiagnosticValues,
    EolStatusValues,
    FirmwareRecommendationValues,
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


ProphylaxisValues = (
    CPUUtilizationValues
    | OSVersionValues
    | UptimeValues
    | SystemMetricValues
    | DiagnosticValues
    | FirmwareRecommendationValues
    | EolStatusValues
    | SerialInventoryValues
    | ModelInventoryValues
)


class ProphylaxisCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str = Field(default="device_facts", min_length=1, max_length=128)
    device_id: int | None = Field(default=None, gt=0)
    device_name: str = Field(min_length=1, max_length=255)
    management_ip: str | None = None
    platform: str | None = None
    status: Literal["success", "failed", "incomplete", "skipped"]
    collection_status: CollectionStatus
    evaluation: EvaluationOutcome
    facts: DeviceFactsValues | None = None
    values: ProphylaxisValues | None = None
    error: str | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_evaluation(cls, value):
        if not isinstance(value, dict):
            return value
        if "evaluation" not in value:
            status = value.get("status")
            inferred = {
                "success": EvaluationOutcome.PASS,
                "failed": EvaluationOutcome.FAIL,
                "incomplete": EvaluationOutcome.UNKNOWN,
                "skipped": EvaluationOutcome.UNSUPPORTED,
            }.get(status)
            if inferred is not None:
                value = {**value, "evaluation": inferred}
        if "collection_status" not in value:
            if value.get("facts") is not None or value.get("values") is not None:
                collection_status = CollectionStatus.COLLECTED
            elif value.get("status") == "skipped":
                collection_status = CollectionStatus.UNSUPPORTED
            else:
                collection_status = CollectionStatus.FAILED
            value = {**value, "collection_status": collection_status}
        return value

    @model_validator(mode="after")
    def validate_payload(self):
        expected_status = {
            EvaluationOutcome.PASS: "success",
            EvaluationOutcome.WARNING: "success",
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
    warning: int = Field(default=0, ge=0)
    failure: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)


class ProphylaxisChart(BaseModel):
    """Best-effort Zabbix chart attached without changing run evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: int = Field(gt=0)
    device_name: str = Field(min_length=1, max_length=255)
    check: str = Field(min_length=1, max_length=128)
    metric: str = Field(min_length=1, max_length=64)
    window: Literal["1h", "24h", "7d", "30d"]
    status: Literal["generated", "unavailable"]
    artifact: str | None = Field(default=None, max_length=128)
    point_count: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_artifact(self):
        if self.status == "generated":
            if (
                not self.artifact
                or self.point_count < 1
                or self.error is not None
            ):
                raise ValueError("generated chart requires an artifact and points")
        elif self.artifact is not None or self.point_count != 0 or not self.error:
            raise ValueError("unavailable chart requires only a safe error code")
        return self


class ProphylaxisDeviceCheck(BaseModel):
    """One check nested under its device in the grouped run representation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str = Field(min_length=1, max_length=128)
    status: Literal["success", "failed", "incomplete", "skipped"]
    collection_status: CollectionStatus
    evaluation: EvaluationOutcome
    facts: DeviceFactsValues | None = None
    values: ProphylaxisValues | None = None
    error: str | None = Field(default=None, max_length=128)


class ProphylaxisDeviceResult(BaseModel):
    """Checks and charts grouped under one unambiguous device identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: int | None = Field(default=None, gt=0)
    device_name: str = Field(min_length=1, max_length=255)
    management_ip: str | None = None
    platform: str | None = None
    summary: RunSummary
    checks: tuple[ProphylaxisDeviceCheck, ...] = ()
    charts: tuple[ProphylaxisChart, ...] = ()


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
    charts: tuple[ProphylaxisChart, ...] = ()
    report_path: str
    error: str | None = Field(default=None, max_length=128)

    @computed_field(return_type=tuple[ProphylaxisDeviceResult, ...])
    @property
    def devices(self) -> tuple[ProphylaxisDeviceResult, ...]:
        """Return the run data grouped by device without changing results."""

        grouped: dict[tuple[int | None, str], dict] = {}
        for result in self.results:
            key = (result.device_id, result.device_name)
            device = grouped.setdefault(
                key,
                {
                    "device_id": result.device_id,
                    "device_name": result.device_name,
                    "management_ip": result.management_ip,
                    "platform": result.platform,
                    "checks": [],
                    "charts": [],
                },
            )
            device["checks"].append(
                ProphylaxisDeviceCheck.model_validate(
                    result.model_dump(
                        exclude={
                            "device_id",
                            "device_name",
                            "management_ip",
                            "platform",
                        }
                    )
                )
            )
        for chart in self.charts:
            key = (chart.device_id, chart.device_name)
            device = grouped.setdefault(
                key,
                {
                    "device_id": chart.device_id,
                    "device_name": chart.device_name,
                    "management_ip": None,
                    "platform": None,
                    "checks": [],
                    "charts": [],
                },
            )
            device["charts"].append(chart)

        devices = []
        for device in grouped.values():
            checks = tuple(device.pop("checks"))
            summary = RunSummary(
                total=len(checks),
                success=sum(
                    check.status == "success"
                    and check.evaluation != EvaluationOutcome.WARNING
                    for check in checks
                ),
                warning=sum(
                    check.evaluation == EvaluationOutcome.WARNING
                    for check in checks
                ),
                failure=sum(
                    check.status in {"failed", "incomplete"}
                    for check in checks
                ),
                skipped=sum(check.status == "skipped" for check in checks),
            )
            devices.append(
                ProphylaxisDeviceResult(
                    **device,
                    summary=summary,
                    checks=checks,
                )
            )
        return tuple(devices)


__all__ = [
    "DeviceFactsResult",
    "ModelInventoryValues",
    "ProphylaxisChart",
    "ProphylaxisCheckResult",
    "ProphylaxisDeviceCheck",
    "ProphylaxisDeviceResult",
    "ProphylaxisRun",
    "RunSummary",
    "SerialInventoryValues",
    "utc_now",
]
