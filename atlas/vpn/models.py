"""Validated, secret-free VPN connection contracts."""

from datetime import datetime
from ipaddress import ip_address, ip_network
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VPNVendor = Literal["fortinet", "checkpoint", "generic"]
VPNProtocol = Literal["ipsec-ikev2", "ssl-vpn", "wireguard", "openvpn"]
VPNDriver = Literal[
    "strongswan", "forticlient", "checkpoint-snx", "wireguard", "openvpn"
]
VPNAuthMethod = Literal["username_password", "certificate", "psk", "external_mfa"]
VPNAdministrativeStatus = Literal["planned", "enabled", "disabled"]
VPNRuntimeStatus = Literal[
    "not_deployed", "disconnected", "connecting", "connected", "degraded", "error"
]
VPNAssignmentTarget = Literal["site", "device"]


def _gateway(value: str) -> str:
    candidate = value.strip().lower()
    if not candidate or len(candidate) > 253:
        raise ValueError("gateway_host is invalid")
    try:
        return str(ip_address(candidate))
    except ValueError:
        pass
    labels = candidate.rstrip(".").split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("gateway_host is invalid")
    return candidate.rstrip(".")


def _prefixes(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        try:
            network = ip_network(value.strip(), strict=True)
        except (AttributeError, ValueError):
            raise ValueError("allowed_prefixes contains an invalid network") from None
        rendered = str(network)
        if rendered not in normalized:
            normalized.append(rendered)
    return normalized


class VPNConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=200)
    tenant_id: int = Field(gt=0)
    vendor: VPNVendor
    protocol: VPNProtocol
    driver: VPNDriver
    gateway_host: str = Field(min_length=1, max_length=253)
    gateway_port: int | None = Field(default=None, ge=1, le=65535)
    auth_method: VPNAuthMethod
    secret_ref: str = Field(min_length=1, max_length=200)
    management_vrf_id: int = Field(gt=0)
    allowed_prefixes: list[str] = Field(min_length=1, max_length=128)
    administrative_status: VPNAdministrativeStatus = "planned"

    @field_validator("gateway_host")
    @classmethod
    def validate_gateway(cls, value: str) -> str:
        return _gateway(value)

    @field_validator("allowed_prefixes")
    @classmethod
    def validate_prefixes(cls, value: list[str]) -> list[str]:
        return _prefixes(value)

    @model_validator(mode="after")
    def validate_secret_reference(self):
        expected = f"vpn/connections/{self.connection_id}"
        if self.secret_ref != expected:
            raise ValueError(f"secret_ref must be {expected}")
        return self


class VPNConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    vendor: VPNVendor | None = None
    protocol: VPNProtocol | None = None
    driver: VPNDriver | None = None
    gateway_host: str | None = Field(default=None, min_length=1, max_length=253)
    gateway_port: int | None = Field(default=None, ge=1, le=65535)
    auth_method: VPNAuthMethod | None = None
    secret_ref: str | None = Field(default=None, min_length=1, max_length=200)
    management_vrf_id: int | None = Field(default=None, gt=0)
    allowed_prefixes: list[str] | None = Field(default=None, min_length=1, max_length=128)
    administrative_status: VPNAdministrativeStatus | None = None

    @field_validator("gateway_host")
    @classmethod
    def validate_gateway(cls, value: str | None) -> str | None:
        return _gateway(value) if value is not None else None

    @field_validator("allowed_prefixes")
    @classmethod
    def validate_prefixes(cls, value: list[str] | None) -> list[str] | None:
        return _prefixes(value) if value is not None else None


class VPNConnectionRecord(VPNConnectionCreate):
    id: UUID
    tenant_slug: str
    runtime_status: VPNRuntimeStatus = "not_deployed"
    created_at: datetime
    updated_at: datetime


class VPNConnectionPage(BaseModel):
    items: list[VPNConnectionRecord]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class VPNValidationResult(BaseModel):
    connection_id: str
    tenant_id: int = Field(gt=0)
    management_vrf_id: int = Field(gt=0)
    secret_available: Literal[True]
    administrative_status: VPNAdministrativeStatus
    runtime_status: Literal["not_deployed"]
    validation_status: Literal["valid"]


class VPNAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: int = Field(gt=0)
    connection_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    target_type: VPNAssignmentTarget
    target_ids: list[int] = Field(min_length=1, max_length=500)
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("target_ids")
    @classmethod
    def validate_target_ids(cls, values: list[int]) -> list[int]:
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("target_ids must contain positive integers")
        if len(set(values)) != len(values):
            raise ValueError("target_ids contains duplicates")
        return values


class VPNResolutionResult(BaseModel):
    device_id: int = Field(gt=0)
    device_name: str | None = None
    tenant_id: int = Field(gt=0)
    tenant_slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    site_id: int = Field(gt=0)
    management_ip: str
    vrf_id: int = Field(gt=0)
    connection_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    connection_source: Literal["device", "site"] | None = None
    administrative_status: VPNAdministrativeStatus | None = None
    runtime_status: Literal["not_deployed"]
    resolution_status: Literal["resolved", "unmapped"]
    vendor: VPNVendor | None = None
    protocol: VPNProtocol | None = None
    driver: VPNDriver | None = None
    gateway_host: str | None = None
    gateway_port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("management_ip")
    @classmethod
    def validate_management_ip(cls, value: str) -> str:
        try:
            return str(ip_address(value))
        except ValueError:
            raise ValueError("management_ip is invalid") from None

    @model_validator(mode="after")
    def validate_resolution(self):
        resolved_values = (
            self.connection_id,
            self.connection_source,
            self.administrative_status,
            self.vendor,
            self.protocol,
            self.driver,
            self.gateway_host,
        )
        if self.resolution_status == "resolved" and any(
            value is None for value in resolved_values
        ):
            raise ValueError("resolved connection metadata is incomplete")
        if self.resolution_status == "unmapped" and any(
            value is not None for value in resolved_values
        ):
            raise ValueError("unmapped result contains connection metadata")
        return self


__all__ = [
    "VPNAssignmentRequest",
    "VPNConnectionCreate",
    "VPNConnectionPage",
    "VPNConnectionPatch",
    "VPNConnectionRecord",
    "VPNResolutionResult",
    "VPNValidationResult",
]
