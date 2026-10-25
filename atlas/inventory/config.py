"""Inventory importer configuration stored in the protected Atlas runtime."""

from pathlib import Path
import os
import re
import tempfile

import yaml


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
    }
    if set(data) - allowed:
        raise ValueError("inventory contains unknown fields")
    if data.get("version", 1) != 1:
        raise ValueError("inventory.version must be 1")
    tenant = data.get("default_tenant")
    if tenant is not None:
        tenant = _mapping(tenant, "inventory.default_tenant")
        if set(tenant) - {"name", "slug", "provision"}:
            raise ValueError("inventory.default_tenant contains unknown fields")
        name = tenant.get("name")
        slug = tenant.get("slug")
        provision = tenant.get("provision", True)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("inventory.default_tenant.name is required")
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", slug
        ):
            raise ValueError("inventory.default_tenant.slug is invalid")
        if type(provision) is not bool:
            raise ValueError("inventory.default_tenant.provision must be boolean")
        tenant = {"name": " ".join(name.split()), "slug": slug, "provision": provision}

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
    role = data.get("default_device_role", "Network Device")
    if role is not None and (not isinstance(role, str) or not role.strip()):
        raise ValueError("inventory.default_device_role is invalid")

    aliases = _mapping(data.get("device_type_aliases"), "inventory.device_type_aliases")
    role_mappings = _mapping(data.get("role_mappings"), "inventory.role_mappings")
    if set(role_mappings) - {"models", "platforms"}:
        raise ValueError("inventory.role_mappings contains unknown sections")
    normalized_mappings = {
        key: _mapping(role_mappings.get(key), f"inventory.role_mappings.{key}")
        for key in ("models", "platforms")
    }
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for mapping in (aliases, *normalized_mappings.values())
        for key, value in mapping.items()
    ):
        raise ValueError("inventory aliases and role mappings must be non-empty strings")
    return {
        "version": 1,
        "default_tenant": tenant,
        "default_site": site,
        "management_interface": interface,
        "default_ipv4_prefix_length": prefix,
        "default_device_role": role.strip() if isinstance(role, str) else None,
        "device_type_aliases": dict(aliases),
        "role_mappings": normalized_mappings,
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
