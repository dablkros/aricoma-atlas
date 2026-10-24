"""Secret-free execution and normalized Profylaxia result contracts."""

from collections.abc import Iterable
from datetime import date, datetime, timezone
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
    SESSION_HEALTH = "session_health"
    STABILITY_EVENTS = "stability_events"
    STORAGE_HEALTH = "storage_health"
    FORTIGUARD_STATUS = "fortiguard_status"
    ACME_STATUS = "acme_status"
    SDWAN_HEALTH = "sdwan_health"
    VPN_TUNNELS = "vpn_tunnels"
    LOG_DELIVERY = "log_delivery"


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
        CheckId.SESSION_HEALTH,
        CheckId.STABILITY_EVENTS,
        CheckId.STORAGE_HEALTH,
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
        CheckId.FORTIGUARD_STATUS,
        CheckId.ACME_STATUS,
    ),
    CheckGroupId.OPERATIONS: (
        CheckId.NTP_SYNC,
        CheckId.RECENT_LOG_EVENTS,
        CheckId.ROUTING_TABLE,
        CheckId.ROUTING_PROTOCOL_NEIGHBORS,
        CheckId.HA_STATUS,
        CheckId.CONFIGURATION_BACKUP,
        CheckId.SDWAN_HEALTH,
        CheckId.VPN_TUNNELS,
        CheckId.LOG_DELIVERY,
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
    FORTIOS_RESOURCES = "fortios_resources"
    FORTIOS_INTERFACES = "fortios_interfaces"
    FORTIOS_COMPLIANCE = "fortios_compliance"
    FORTIOS_OPERATIONS = "fortios_operations"
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
    collected_at: datetime | None = None
    context: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    evidence: tuple[str, ...] = ()
    evaluation_rule: str | None = Field(default=None, max_length=512)
    recommended_action: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_evaluation(self):
        if self.outcome == EvaluationOutcome.PASS and self.reason_code is not None:
            raise ValueError("passing diagnostic must not contain a reason code")
        if self.outcome != EvaluationOutcome.PASS and self.reason_code is None:
            raise ValueError("non-passing diagnostic requires a reason code")
        return self


class FirmwareRecommendationValues(BaseModel):
    """Cisco suggested-release comparison for one verified device PID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    reason_code: str = Field(min_length=1, max_length=128)
    product_id: str = Field(min_length=1, max_length=255)
    installed_release: str = Field(min_length=1, max_length=255)
    suggested_releases: tuple[str, ...] = ()
    software_type: str | None = Field(default=None, max_length=255)
    source: Literal["cisco_software_suggestion_v2"] = (
        "cisco_software_suggestion_v2"
    )


class EoxLifecycleRecord(BaseModel):
    """Normalized lifecycle milestones from one Cisco EoX record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["hardware", "software"]
    product_id: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    bulletin_number: str | None = Field(default=None, max_length=128)
    bulletin_url: str | None = Field(default=None, max_length=2048)
    announcement_date: date | None = None
    end_of_sale_date: date | None = None
    end_of_software_maintenance_date: date | None = None
    end_of_security_support_date: date | None = None
    last_date_of_support: date | None = None
    updated_date: date | None = None


class EolStatusValues(BaseModel):
    """Combined hardware and software lifecycle assessment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EvaluationOutcome
    reason_code: str = Field(min_length=1, max_length=128)
    serial_number: str = Field(min_length=1, max_length=255)
    product_id: str = Field(min_length=1, max_length=255)
    installed_release: str = Field(min_length=1, max_length=255)
    os_type: Literal["IOS", "IOS-XE", "NX-OS", "ASA"] | None = None
    hardware_state: Literal["record_found", "no_record", "unavailable"]
    software_state: Literal["record_found", "no_record", "unavailable"]
    hardware_record_count: int = Field(default=0, ge=0)
    software_record_count: int = Field(default=0, ge=0)
    records_truncated: bool = False
    records: tuple[EoxLifecycleRecord, ...] = ()
    source: Literal["cisco_eox_v5"] = "cisco_eox_v5"


class RawCollectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    collector: CollectorId
    platform: str
    status: Literal["ok", "error", "unsupported"]
    data: Any | None = None
    raw_output: str | None = None
    error_code: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

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
    "EolStatusValues",
    "EoxLifecycleRecord",
    "FirmwareRecommendationValues",
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
