"""Reusable NetBox/OpenBao contract for a future Atlas orchestrator.

No inventory writes, HTTP routes, credential fallback or OS inference here.
Callers must keep returned secrets in memory and out of logs/NetBox.
"""

from dataclasses import dataclass, field
from ipaddress import ip_interface
import re

from atlas.openbao_client import OpenBaoError, OpenBaoNotFound
from atlas.platforms import load_platform_config


class DeviceValidationError(ValueError):
    def __init__(
        self,
        message,
        *,
        code="invalid_device",
        category="skipped",
    ):
        self.code = code
        self.category = category
        super().__init__(message)


def credential_path(vendor, profile, purpose="admin"):
    # Reject unsafe/noncanonical input instead of silently changing secret identity.
    if not isinstance(vendor, str) or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", vendor):
        raise DeviceValidationError(
            "Invalid manufacturer slug",
            code="missing_manufacturer",
        )
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile):
        raise DeviceValidationError(
            "Invalid credential_profile",
            code="missing_credential_profile",
        )
    if purpose not in {"admin", "snmp", "automation"}:
        raise DeviceValidationError("Invalid credential purpose")
    return f"devices/credentials/{vendor}/{profile}/{purpose}"


def validate_admin_secret(secret):
    if not isinstance(secret, dict) or any(
        not isinstance(secret.get(key), str) or not secret[key].strip()
        for key in ("username", "password")
    ) or ("enable_password" in secret and not isinstance(secret["enable_password"], str)):
        raise DeviceValidationError(
            "Invalid admin credential schema",
            code="invalid_credential_schema",
            category="error",
        )
    return {key: secret[key] for key in ("username", "password", "enable_password") if key in secret}


def validate_snmp_secret(secret):
    """Validate stored shape, not a claim of protocol support by a collector."""
    if not isinstance(secret, dict) or secret.get("version") not in ("2c", "3"):
        raise DeviceValidationError("Invalid SNMP credential schema")
    keys = ("community",) if secret["version"] == "2c" else (
        "username", "auth_protocol", "auth_password", "privacy_protocol", "privacy_password")
    if any(not isinstance(secret.get(key), str) or not secret[key].strip() for key in keys):
        raise DeviceValidationError("Invalid SNMP credential schema")
    return {key: secret[key] for key in ("version", *keys)}


@dataclass(frozen=True)
class DeviceReference:
    name: str
    address: str
    platform: str
    model: str
    vendor: str
    profile: str
    path: str


@dataclass(frozen=True)
class ResolvedDevice:
    reference: DeviceReference
    # Avoid accidental leakage via repr/logging the result object.
    credentials: dict = field(repr=False)


def oxidized_reference(device, config=None):
    """None for disabled devices. Enabled invalid devices fail before secret access."""
    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    if fields.get("oxidized_enabled") is not True:
        return None
    platform_data = device.get("platform")
    platform = platform_data.get("slug") if isinstance(platform_data, dict) else None
    if not isinstance(platform, str) or not platform:
        raise DeviceValidationError(
            "Missing NetBox platform",
            code="missing_platform",
        )
    config = load_platform_config() if config is None else config
    model = config["oxidized_platform_mapping"].get(platform, {}).get("model")
    if not model:
        raise DeviceValidationError(
            f"No Oxidized mapping for platform {platform}",
            code="unsupported_platform",
        )
    profile = fields.get("credential_profile")
    if not profile:
        raise DeviceValidationError(
            "Missing credential_profile",
            code="missing_credential_profile",
        )
    # NetBox Device REST serializer nests manufacturer under device_type.
    device_type = device.get("device_type")
    manufacturer = (
        device_type.get("manufacturer")
        if isinstance(device_type, dict)
        else None
    )
    vendor = manufacturer.get("slug") if isinstance(manufacturer, dict) else None
    if not isinstance(vendor, str) or not vendor:
        raise DeviceValidationError(
            "Missing manufacturer",
            code="missing_manufacturer",
        )
    path = credential_path(vendor, profile)
    name = device.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DeviceValidationError("Missing device name", code="missing_name")
    primary = (
        device.get("primary_ip4")
        or device.get("primary_ip6")
        or device.get("primary_ip")
        or {}
    )
    address_value = primary.get("address", "") if isinstance(primary, dict) else ""
    try:
        address = str(ip_interface(address_value).ip)
    except (TypeError, ValueError):
        raise DeviceValidationError(
            "Missing or invalid primary IP",
            code="missing_primary_ip",
        ) from None
    return DeviceReference(name.strip(), address, platform, model, vendor, profile, path)


def resolve_oxidized_device(device, credential_source, token=None, config=None):
    reference = oxidized_reference(device, config)
    if reference is None:
        return None
    if token is None and hasattr(credential_source, "get_device_credentials"):
        credentials = credential_source.get_device_credentials(
            reference.vendor,
            reference.profile,
        )
        return ResolvedDevice(reference, credentials)

    try:
        secret = credential_source.kv_read(
            token,
            reference.path,
            mount="atlas",
        )
    except OpenBaoNotFound:
        raise DeviceValidationError(
            "Credential profile not found",
            code="credential_not_found",
            category="error",
        ) from None
    except OpenBaoError:
        raise DeviceValidationError(
            "Unable to read credential profile from OpenBao",
            code="credential_unavailable",
            category="error",
        ) from None
    return ResolvedDevice(reference, validate_admin_secret(secret))
