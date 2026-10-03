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
    pass


def credential_path(vendor, profile, purpose="admin"):
    # Reject unsafe/noncanonical input instead of silently changing secret identity.
    if not isinstance(vendor, str) or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", vendor):
        raise DeviceValidationError("Invalid manufacturer slug")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile):
        raise DeviceValidationError("Invalid credential_profile")
    if purpose not in {"admin", "snmp", "automation"}:
        raise DeviceValidationError("Invalid credential purpose")
    return f"devices/credentials/{vendor}/{profile}/{purpose}"


def validate_admin_secret(secret):
    if not isinstance(secret, dict) or any(
        not isinstance(secret.get(key), str) or not secret[key].strip()
        for key in ("username", "password")
    ) or ("enable_password" in secret and not isinstance(secret["enable_password"], str)):
        raise DeviceValidationError("Invalid admin credential schema")
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
    fields = device.get("custom_fields") or {}
    if fields.get("oxidized_enabled") is not True:
        return None
    platform = (device.get("platform") or {}).get("slug")
    if not platform:
        raise DeviceValidationError("Missing NetBox platform")
    config = load_platform_config() if config is None else config
    model = config["oxidized_platform_mapping"].get(platform, {}).get("model")
    if not model:
        raise DeviceValidationError(f"No Oxidized mapping for platform {platform}")
    profile = fields.get("credential_profile")
    if not profile:
        raise DeviceValidationError("Missing credential_profile")
    # NetBox Device REST serializer nests manufacturer under device_type.
    vendor = ((device.get("device_type") or {}).get("manufacturer") or {}).get("slug")
    path = credential_path(vendor, profile)
    name = device.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DeviceValidationError("Missing device name")
    primary = device.get("primary_ip4") or device.get("primary_ip6") or device.get("primary_ip") or {}
    try:
        address = str(ip_interface(primary.get("address", "")).ip)
    except ValueError:
        raise DeviceValidationError("Missing or invalid primary IP") from None
    return DeviceReference(name, address, platform, model, vendor, profile, path)


def resolve_oxidized_device(device, client, token, config=None):
    reference = oxidized_reference(device, config)
    if reference is None:
        return None
    try:
        secret = client.kv_read(token, reference.path, mount="atlas")
    except OpenBaoNotFound:
        raise DeviceValidationError("Credential profile not found") from None
    except OpenBaoError:
        raise DeviceValidationError("Unable to read credential profile from OpenBao") from None
    return ResolvedDevice(reference, validate_admin_secret(secret))
