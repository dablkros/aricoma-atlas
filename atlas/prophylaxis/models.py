"""Secret-free execution and normalized Profylaxia result contracts."""

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = 1
CHECK_VALUE_ALIASES = {
    "CPU utilization": "cpu_utilization",
    "Memory utilization": "memory_utilization",
    "Temperature": "cpu_temperature",
    "Interface utilization": "interface_utilization",
    "interface errors": "interface_error_counters",
    "Routing table": "routing_table",
    "Routing protocol neighbors": "routing_protocol_neighbors",
    "HA status": "ha_status",
}


class CheckId(StrEnum):
    DEVICE_FACTS = "device_facts"
    CPU_UTILIZATION = "cpu_utilization"
    OS_VERSION = "os_version"
    CPU_TEMPERATURE = "cpu_temperature"
    MEMORY_UTILIZATION = "memory_utilization"
    FAN_HEALTH = "fan_health"
    UPTIME = "uptime"
    INTERFACE_UTILIZATION = "interface_utilization"
    INTERFACE_STATE = "interface_state"
    INTERFACE_ERROR_COUNTERS = "interface_error_counters"
    ENVIRONMENT_HEALTH = "environment_health"
    LICENSE_STATE = "license_state"
    PKI_CERTIFICATES = "pki_certificates"
    FIRMWARE_RECOMMENDATION = "firmware_recommendation"
    EOL_STATUS = "eol_status"
    NTP_SYNC = "ntp_sync"
    RECENT_LOG_EVENTS = "recent_log_events"
    ROUTING_TABLE = "routing_table"
    ROUTING_PROTOCOL_NEIGHBORS = "routing_protocol_neighbors"
    HA_STATUS = "ha_status"
    CONFIGURATION_BACKUP = "configuration_backup"
    SERIAL_INVENTORY = "serial_inventory"
    MODEL_INVENTORY = "model_inventory"


class CheckGroupId(StrEnum):
    IDENTITY = "identity"
    RESOURCES = "resources"
    INTERFACES = "interfaces"
    COMPLIANCE = "compliance"
    OPERATIONS = "operations"


CHECK_GROUP_CHECKS = {
    CheckGroupId.IDENTITY: (
        CheckId.DEVICE_FACTS,
        CheckId.OS_VERSION,
        CheckId.UPTIME,
    ),
    CheckGroupId.RESOURCES: (
        CheckId.CPU_UTILIZATION,
        CheckId.MEMORY_UTILIZATION,
        CheckId.ENVIRONMENT_HEALTH,
        CheckId.CPU_TEMPERATURE,
        CheckId.FAN_HEALTH,
    ),
    CheckGroupId.INTERFACES: (
        CheckId.INTERFACE_STATE,
        CheckId.INTERFACE_UTILIZATION,
        CheckId.INTERFACE_ERROR_COUNTERS,
    ),
    CheckGroupId.COMPLIANCE: (
        CheckId.LICENSE_STATE,
        CheckId.PKI_CERTIFICATES,
        CheckId.FIRMWARE_RECOMMENDATION,
        CheckId.EOL_STATUS,
    ),
    CheckGroupId.OPERATIONS: (
        CheckId.NTP_SYNC,
        CheckId.RECENT_LOG_EVENTS,
        CheckId.ROUTING_TABLE,
        CheckId.ROUTING_PROTOCOL_NEIGHBORS,
        CheckId.HA_STATUS,
        CheckId.CONFIGURATION_BACKUP,
    ),
}


def expand_check_selection(
    raw_values: Iterable[str],
) -> tuple[tuple[CheckId, ...], tuple[str, ...]]:
    """Expand NetBox groups and compatible legacy check values."""
    values = set(raw_values)
    checks: list[CheckId] = []
    unknown: list[str] = []

    for group_id in CheckGroupId:
        if group_id.value not in values:
            continue
        for check_id in CHECK_GROUP_CHECKS[group_id]:
            if check_id not in checks:
                checks.append(check_id)

    group_values = {group_id.value for group_id in CheckGroupId}
    for value in sorted(values - group_values):
        normalized = CHECK_VALUE_ALIASES.get(value, value)
        try:
            check_id = CheckId(normalized)
        except ValueError:
            unknown.append(value[:128])
            continue
        if check_id not in checks:
            checks.append(check_id)
    return tuple(checks), tuple(unknown)


class CollectorId(StrEnum):
    IOS_IDENTITY = "ios_identity"
    IOS_RESOURCES = "ios_resources"
    IOS_INTERFACES = "ios_interfaces"
    IOS_COMPLIANCE = "ios_compliance"
    IOS_OPERATIONS = "ios_operations"
    CBS_RESOURCES = "cbs_resources"
    CBS_INTERFACES = "cbs_interfaces"
    CBS_COMPLIANCE = "cbs_compliance"
    CBS_OPERATIONS = "cbs_operations"
    # Compatibility collector IDs used by other platform profiles and the
    # synchronous check endpoint. New cisco-ios plans prefer the ios_* IDs.
    DEVICE_FACTS = "device_facts"
    CPU_USAGE = "cpu_usage"
    SNMP_SYSTEM = "snmp_system"


class CollectionStatus(StrEnum):
    COLLECTED = "COLLECTED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class EvaluationOutcome(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class DeviceFactsValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hostname: str = Field(min_length=1, max_length=255)
    model: str = Field(min_length=1, max_length=255)
    os_version: str = Field(min_length=1, max_length=255)
    serial_number: str | None = Field(default=None, min_length=1, max_length=255)
    uptime_seconds: int | None = Field(default=None, ge=0)


class OSVersionValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    os_version: str = Field(min_length=1, max_length=255)


class UptimeValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uptime_seconds: int = Field(ge=0)
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)


class CPUUtilizationValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_percent: float = Field(ge=0, le=100)
    one_minute_percent: float | None = Field(default=None, ge=0, le=100)
    five_minute_percent: float | None = Field(default=None, ge=0, le=100)


class HardwareSensorValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1)
    status_code: int | None = None
    status_text: str
    outcome: EvaluationOutcome


class SystemMetricValues(BaseModel):
    """Normalized result for metrics that can be unavailable or inconclusive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    value: float | None = None
    unit: Literal["percent", "celsius"] | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    sys_object_id: str | None = Field(default=None, min_length=1, max_length=255)
    expected_components: int | None = Field(default=None, ge=0)
    sensors: tuple[HardwareSensorValue, ...] = ()

    @model_validator(mode="after")
    def validate_evaluation(self):
        if self.outcome == EvaluationOutcome.PASS and self.reason_code is not None:
            raise ValueError("passing metric must not contain a reason code")
        if self.outcome != EvaluationOutcome.PASS and self.reason_code is None:
            raise ValueError("non-passing metric requires a reason code")
        return self


class DiagnosticValues(BaseModel):
    """Normalized, secret-free result for non-scalar diagnostic checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=512)
    metrics: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    affected_items: tuple[str, ...] = ()
    source_commands: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evaluation(self):
        if self.outcome == EvaluationOutcome.PASS and self.reason_code is not None:
            raise ValueError("passing diagnostic must not contain a reason code")
        if self.outcome != EvaluationOutcome.PASS and self.reason_code is None:
            raise ValueError("non-passing diagnostic requires a reason code")
        return self


class RawCollectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    collector: CollectorId
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


class RawCheckResult(BaseModel):
    """Legacy CPU execution contract kept for existing callers and fixtures."""

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
    "CHECK_GROUP_CHECKS",
    "CHECK_VALUE_ALIASES",
    "CPUCheckResult",
    "CPUUtilizationValues",
    "CheckGroupId",
    "CheckId",
    "CheckResult",
    "CollectionStatus",
    "CollectorId",
    "EvaluationOutcome",
    "HardwareSensorValue",
    "DeviceFactsValues",
    "DiagnosticValues",
    "OSVersionValues",
    "RawCheckResult",
    "RawCollectorResult",
    "SCHEMA_VERSION",
    "StoredCPUCheckResult",
    "StoredCheckResult",
    "SystemMetricValues",
    "UptimeValues",
    "expand_check_selection",
]
