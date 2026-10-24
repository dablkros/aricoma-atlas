"""Validated NetBox policy input for platform-aware Profylaxia evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.device_credentials import DeviceValidationError


class ThresholdExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_warning_percent: float | None = Field(default=None, ge=0, le=100)
    cpu_critical_percent: float | None = Field(default=None, ge=0, le=100)
    memory_warning_percent: float | None = Field(default=None, ge=0, le=100)
    memory_critical_percent: float | None = Field(default=None, ge=0, le=100)
    interface_utilization_warning_percent: float | None = Field(
        default=None, ge=0, le=100
    )
    certificate_warning_days: int = Field(default=60, ge=1, le=3650)
    certificate_critical_days: int = Field(default=30, ge=0, le=3650)
    sdwan_packet_loss_warning_percent: float | None = Field(
        default=None, ge=0, le=100
    )
    sdwan_latency_warning_ms: float | None = Field(default=None, ge=0)
    sdwan_jitter_warning_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_threshold_order(self):
        for warning_name, critical_name in (
            ("cpu_warning_percent", "cpu_critical_percent"),
            ("memory_warning_percent", "memory_critical_percent"),
        ):
            warning = getattr(self, warning_name)
            critical = getattr(self, critical_name)
            if warning is not None and critical is not None and warning > critical:
                raise ValueError(f"{warning_name} must not exceed {critical_name}")
        if self.certificate_critical_days > self.certificate_warning_days:
            raise ValueError(
                "certificate_critical_days must not exceed certificate_warning_days"
            )
        return self


class InterfaceExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_up: tuple[str, ...] = ()


class RoutingExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_routes: tuple[str, ...] = ()
    bgp_neighbors: tuple[str, ...] = ()
    ospf_neighbors: tuple[str, ...] = ()


class HAExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["standalone", "active-passive", "active-active"] | None = None
    members: tuple[str, ...] = ()
    monitored_interfaces: tuple[str, ...] = ()


class VPNExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_up: tuple[str, ...] = ()
    on_demand: tuple[str, ...] = ()


class SDWANExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    members: tuple[str, ...] = ()
    health_checks: tuple[str, ...] = ()


class FortiOSExpectations(BaseModel):
    """Optional policy. Empty sections never imply operational PASS or FAIL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thresholds: ThresholdExpectations = Field(default_factory=ThresholdExpectations)
    interfaces: InterfaceExpectations = Field(default_factory=InterfaceExpectations)
    routing: RoutingExpectations = Field(default_factory=RoutingExpectations)
    ha: HAExpectations = Field(default_factory=HAExpectations)
    vpn: VPNExpectations = Field(default_factory=VPNExpectations)
    sdwan: SDWANExpectations = Field(default_factory=SDWANExpectations)
    vdoms: tuple[str, ...] = ()

    def context(self) -> dict:
        return self.model_dump(mode="json")


def fortios_expectations(device: dict) -> FortiOSExpectations:
    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    raw = fields.get("profylaxia_expectations")
    if raw in (None, {}):
        return FortiOSExpectations()
    if not isinstance(raw, dict):
        raise DeviceValidationError(
            "Profylaxia expectations must be a JSON object",
            code="invalid_prophylaxis_expectations",
            category="error",
        )
    try:
        return FortiOSExpectations.model_validate(raw)
    except ValidationError:
        raise DeviceValidationError(
            "Profylaxia expectations do not match the supported schema",
            code="invalid_prophylaxis_expectations",
            category="error",
        ) from None


__all__ = ["FortiOSExpectations", "fortios_expectations"]
