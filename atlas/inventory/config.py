"""Inventory importer configuration stored in the protected Atlas runtime."""

from pathlib import Path
import os
import re
import tempfile

import yaml


DEFAULT_ROLE_MAPPINGS = {
    "models": {
        "Nexus 93360YC-FX2": "switch",
        "Catalyst 9500-48Y4C": "switch",
        "Catalyst 9300L-24P-4G": "switch",
        "Catalyst 9200L-24T-4X": "switch",
        "Smart-1 600-M": "management-appliance",
        "SN2410": "switch",
        "SG9700-PLUS": "firewall",
        "F5 C131": "load-balancer",
    },
    "platforms": {
        "cisco-nxos": "switch",
        "cisco-cbs": "switch",
        "cisco-cbs250-350": "switch",
        "fortios": "firewall",
        "sophos-sfos": "firewall",
        "aruba-aos-cx": "switch",
        "aruba-aos-switch": "switch",
        "checkpoint-gaia-embedded": "firewall",
    },
}


def _mapping(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def normalize_inventory_config(raw: object) -> dict:
    data = _mapping(raw, "inventory")
    allowed = {
        "version",
        "default_tenant",
        "default_site",
        "management_interface",
        "default_ipv4_prefix_length",
        "default_device_role",
        "device_type_aliases",
        "role_mappings",
        "connection_profiles",
    }
    if set(data) - allowed:
        raise ValueError("inventory contains unknown fields")
    if data.get("version", 1) != 1:
        raise ValueError("inventory.version must be 1")
    tenant = data.get("default_tenant")
    if tenant is not None:
        tenant = _mapping(tenant, "inventory.default_tenant")
        if set(tenant) - {"name", "slug", "group", "provision"}:
            raise ValueError("inventory.default_tenant contains unknown fields")
        name = tenant.get("name")
        slug = tenant.get("slug")
        provision = tenant.get("provision", True)
        group = tenant.get("group")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("inventory.default_tenant.name is required")
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", slug
        ):
            raise ValueError("inventory.default_tenant.slug is invalid")
        if type(provision) is not bool:
            raise ValueError("inventory.default_tenant.provision must be boolean")
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise ValueError("inventory.default_tenant.group is invalid")
        tenant = {
            "name": " ".join(name.split()), "slug": slug,
            "group": " ".join(group.split()) if group else None,
            "provision": provision,
        }

    site = data.get("default_site")
    if site is not None:
        site = _mapping(site, "inventory.default_site")
        if set(site) - {"name", "slug", "provision"}:
            raise ValueError("inventory.default_site contains unknown fields")
        name = site.get("name")
        slug = site.get("slug")
        provision = site.get("provision", True)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("inventory.default_site.name is required")
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", slug
        ):
            raise ValueError("inventory.default_site.slug is invalid")
        if type(provision) is not bool:
            raise ValueError("inventory.default_site.provision must be boolean")
        site = {"name": " ".join(name.split()), "slug": slug, "provision": provision}
        if tenant is None:
            raise ValueError("inventory.default_site requires default_tenant")

    interface = data.get("management_interface", "mgmt0")
    if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,64}", interface):
        raise ValueError("inventory.management_interface is invalid")
    prefix = data.get("default_ipv4_prefix_length", 32)
    if isinstance(prefix, bool) or not isinstance(prefix, int) or not 0 <= prefix <= 32:
        raise ValueError("inventory.default_ipv4_prefix_length must be 0..32")
    role = data.get("default_device_role", "network-device")
    if role is not None and (not isinstance(role, str) or not role.strip()):
        raise ValueError("inventory.default_device_role is invalid")

    aliases = _mapping(data.get("device_type_aliases"), "inventory.device_type_aliases")
    role_mappings = _mapping(data.get("role_mappings"), "inventory.role_mappings")
    if set(role_mappings) - {"models", "platforms"}:
        raise ValueError("inventory.role_mappings contains unknown sections")
    normalized_mappings = {}
    for key in ("models", "platforms"):
        configured = _mapping(
            role_mappings.get(key),
            f"inventory.role_mappings.{key}",
        )
        normalized_mappings[key] = {
            **DEFAULT_ROLE_MAPPINGS[key],
            **configured,
        }
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for mapping in (aliases, *normalized_mappings.values())
        for key, value in mapping.items()
    ):
        raise ValueError("inventory aliases and role mappings must be non-empty strings")

    profiles = _mapping(data.get("connection_profiles"), "inventory.connection_profiles")
    normalized_profiles = {}
    for connection_id, raw_profile in profiles.items():
        if not isinstance(connection_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", connection_id
        ):
            raise ValueError("inventory.connection_profiles contains an invalid ID")
        profile = _mapping(
            raw_profile,
            f"inventory.connection_profiles.{connection_id}",
        )
        if set(profile) - {"tenant", "status", "vrf", "transport"}:
            raise ValueError(
                f"inventory.connection_profiles.{connection_id} contains unknown fields"
            )
        tenant_slug = profile.get("tenant")
        status = profile.get("status", "planned")
        vrf = profile.get("vrf")
        transport = profile.get("transport")
        if not isinstance(tenant_slug, (str, int)) or isinstance(tenant_slug, bool) or (
            isinstance(tenant_slug, str)
            and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tenant_slug)
        ):
            raise ValueError(
                f"inventory.connection_profiles.{connection_id}.tenant is invalid"
            )
        if status not in {"planned", "active", "disabled"}:
            raise ValueError(
                f"inventory.connection_profiles.{connection_id}.status is invalid"
            )
        if not isinstance(vrf, (str, int)) or isinstance(vrf, bool) or not str(vrf).strip():
            raise ValueError(
                f"inventory.connection_profiles.{connection_id}.vrf is required"
            )
        if transport is not None and (
            not isinstance(transport, str)
            or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", transport)
        ):
            raise ValueError(
                f"inventory.connection_profiles.{connection_id}.transport is invalid"
            )
        normalized_profiles[connection_id] = {
            "tenant": str(tenant_slug),
            "status": status,
            "vrf": str(vrf).strip(),
            "transport": transport,
        }
    return {
        "version": 1,
        "default_tenant": tenant,
        "default_site": site,
        "management_interface": interface,
        "default_ipv4_prefix_length": prefix,
        "default_device_role": role.strip() if isinstance(role, str) else None,
        "device_type_aliases": dict(aliases),
        "role_mappings": normalized_mappings,
        "connection_profiles": normalized_profiles,
    }


def load_inventory_config(path: Path) -> dict:
    if not path.exists():
        return normalize_inventory_config({})
    return normalize_inventory_config(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_inventory_config(path: Path, config: dict) -> None:
    normalized = normalize_inventory_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            yaml.safe_dump(normalized, handle, sort_keys=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
