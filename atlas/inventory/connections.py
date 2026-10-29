"""Resolve a device to one explicit tenant-owned Atlas connection profile."""

from __future__ import annotations

import ipaddress

from atlas.inventory.importer import related_id


class ConnectionMappingError(ValueError):
    """A device connection mapping is inconsistent or unsafe to use."""


def _custom_field(value: object, name: str) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(name)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


class ConnectionResolver:
    def __init__(self, client, config: dict):
        self.client = client
        self.profiles = config.get("connection_profiles", {})

    def _one(self, endpoint: str, params: dict, missing: str, ambiguous: str) -> dict:
        values = self.client.get_all(endpoint, params=params)
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ConnectionMappingError("netbox_invalid_response")
        if len(values) != 1:
            raise ConnectionMappingError(missing if not values else ambiguous)
        return values[0]

    def resolve(
        self, device_id: int, *, expected_tenant: str | int | None = None
    ) -> dict:
        device = self._one(
            "/api/dcim/devices/", {"id": device_id},
            "device_not_found", "device_ambiguous",
        )
        tenant = device.get("tenant")
        site = device.get("site")
        if not isinstance(tenant, dict) or related_id(tenant) is None:
            raise ConnectionMappingError("device_tenant_missing")
        if expected_tenant is not None and str(expected_tenant) not in {
            str(related_id(tenant)), str(tenant.get("slug", "")),
        }:
            raise ConnectionMappingError("device_outside_requested_tenant")
        if not isinstance(site, dict) or related_id(site) is None:
            raise ConnectionMappingError("device_site_missing")
        site = self._one(
            "/api/dcim/sites/", {"id": related_id(site)},
            "site_not_found", "site_ambiguous",
        )
        if related_id(site.get("tenant")) != related_id(tenant):
            raise ConnectionMappingError("site_tenant_mismatch")

        primary = device.get("primary_ip4")
        primary_id = related_id(primary)
        if primary_id is None:
            raise ConnectionMappingError("management_ip_missing")
        address = self._one(
            "/api/ipam/ip-addresses/", {"id": primary_id},
            "management_ip_not_found", "management_ip_ambiguous",
        )
        if related_id(address.get("tenant")) != related_id(tenant):
            raise ConnectionMappingError("management_ip_tenant_mismatch")
        vrf = address.get("vrf")
        vrf_id = related_id(vrf)
        if vrf_id is None:
            raise ConnectionMappingError("management_vrf_missing")
        vrf = self._one(
            "/api/ipam/vrfs/", {"id": vrf_id},
            "management_vrf_not_found", "management_vrf_ambiguous",
        )
        if related_id(vrf.get("tenant")) != related_id(tenant):
            raise ConnectionMappingError("management_vrf_tenant_mismatch")
        try:
            management_ip = str(ipaddress.ip_interface(str(address["address"])).ip)
        except (KeyError, ValueError):
            raise ConnectionMappingError("management_ip_invalid") from None

        device_connection = _custom_field(device.get("custom_fields"), "atlas_connection_id")
        site_connection = _custom_field(site.get("custom_fields"), "atlas_connection_id")
        connection_id = device_connection or site_connection
        source = "device" if device_connection else "site" if site_connection else None
        base = {
            "device": {"id": device_id, "name": device.get("name")},
            "tenant": {"id": related_id(tenant), "slug": tenant.get("slug")},
            "site": {"id": site.get("id"), "slug": site.get("slug")},
            "management_ip": management_ip,
            "vrf": {"id": vrf.get("id"), "name": vrf.get("name"), "rd": vrf.get("rd")},
            "connection_id": connection_id,
            "connection_source": source,
        }
        if connection_id is None:
            return {**base, "connection_status": "unmapped", "transport": None}
        profile = self.profiles.get(connection_id)
        if not isinstance(profile, dict):
            raise ConnectionMappingError("connection_profile_not_found")
        profile_tenant = str(profile.get("tenant", ""))
        if profile_tenant not in {str(related_id(tenant)), str(tenant.get("slug", ""))}:
            raise ConnectionMappingError("connection_tenant_mismatch")
        profile_vrf = str(profile.get("vrf", ""))
        actual_vrf = {str(vrf.get("id")), str(vrf.get("name", "")), str(vrf.get("rd", ""))}
        if profile_vrf not in actual_vrf:
            raise ConnectionMappingError("connection_vrf_mismatch")
        return {
            **base,
            "connection_status": profile["status"],
            "transport": profile.get("transport"),
        }


__all__ = ["ConnectionMappingError", "ConnectionResolver"]
