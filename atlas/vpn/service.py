"""VPN registry validation and tenant-safe device resolution."""

from ipaddress import ip_interface, ip_network
import re

from pydantic import ValidationError

from atlas.inventory.importer import related_id
from atlas.services.errors import DependencyError
from atlas.vpn.models import (
    VPNConnectionCreate,
    VPNConnectionPage,
    VPNConnectionPatch,
    VPNConnectionRecord,
)
from atlas.vpn.store import (
    SQLiteVPNConnectionStore,
    VPNConnectionConflict,
    VPNConnectionNotFound,
    VPNStoreError,
)


class VPNRegistryError(RuntimeError):
    def __init__(self, code: str, status_code: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _custom_field(value: object, name: str) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(name)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


class VPNRegistryService:
    def __init__(self, store: SQLiteVPNConnectionStore, netbox, openbao) -> None:
        self.store = store
        self.netbox = netbox
        self.openbao = openbao

    def _one(self, endpoint: str, params: dict, missing: str, ambiguous: str) -> dict:
        try:
            values = self.netbox.get_all(endpoint, params=params)
        except DependencyError:
            raise VPNRegistryError("netbox_unavailable", 503) from None
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise VPNRegistryError("netbox_invalid_response", 503)
        if len(values) != 1:
            raise VPNRegistryError(missing if not values else ambiguous, 404 if not values else 409)
        return values[0]

    def _tenant(self, tenant_id: int) -> dict:
        tenant = self._one(
            "/api/tenancy/tenants/", {"id": tenant_id},
            "tenant_not_found", "tenant_ambiguous",
        )
        slug = tenant.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", slug
        ):
            raise VPNRegistryError("tenant_slug_invalid", 503)
        return tenant

    def _validate_references(self, value: VPNConnectionCreate) -> dict:
        tenant = self._tenant(value.tenant_id)
        vrf = self._one(
            "/api/ipam/vrfs/", {"id": value.management_vrf_id},
            "management_vrf_not_found", "management_vrf_ambiguous",
        )
        if related_id(vrf.get("tenant")) != value.tenant_id:
            raise VPNRegistryError("management_vrf_tenant_mismatch", 409)
        return tenant

    @staticmethod
    def _record(raw: dict, tenant_slug: str) -> VPNConnectionRecord:
        try:
            return VPNConnectionRecord(**raw, tenant_slug=tenant_slug)
        except ValidationError:
            raise VPNRegistryError("vpn_registry_invalid_record", 503) from None

    def create(self, value: VPNConnectionCreate) -> VPNConnectionRecord:
        tenant = self._validate_references(value)
        try:
            return self.store.create(value, tenant["slug"])
        except VPNConnectionConflict:
            raise VPNRegistryError("connection_id_conflict", 409) from None
        except VPNStoreError:
            raise VPNRegistryError("vpn_registry_unavailable", 503) from None

    def get(self, connection_id: str) -> VPNConnectionRecord:
        try:
            raw = self.store.raw(connection_id)
        except VPNConnectionNotFound:
            raise VPNRegistryError("connection_not_found", 404) from None
        except VPNStoreError:
            raise VPNRegistryError("vpn_registry_unavailable", 503) from None
        tenant = self._tenant(raw["tenant_id"])
        return self._record(raw, tenant["slug"])

    def list(
        self, *, tenant_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> VPNConnectionPage:
        if (
            (tenant_id is not None and (isinstance(tenant_id, bool) or tenant_id <= 0))
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise VPNRegistryError("pagination_invalid")
        if tenant_id is not None:
            self._tenant(tenant_id)
        try:
            rows, total = self.store.list_raw(
                tenant_id=tenant_id, limit=limit, offset=offset,
            )
        except VPNStoreError:
            raise VPNRegistryError("vpn_registry_unavailable", 503) from None
        tenants: dict[int, str] = {}
        records = []
        for row in rows:
            owner = row["tenant_id"]
            if owner not in tenants:
                tenants[owner] = self._tenant(owner)["slug"]
            records.append(self._record(row, tenants[owner]))
        return VPNConnectionPage(items=records, total=total, limit=limit, offset=offset)

    def patch(
        self, connection_id: str, changes: VPNConnectionPatch
    ) -> VPNConnectionRecord:
        if not changes.model_fields_set:
            raise VPNRegistryError("connection_patch_empty")
        try:
            current = self.store.raw(connection_id)
        except VPNConnectionNotFound:
            raise VPNRegistryError("connection_not_found", 404) from None
        except VPNStoreError:
            raise VPNRegistryError("vpn_registry_unavailable", 503) from None
        merged = {
            key: value
            for key, value in current.items()
            if key not in {"id", "created_at", "updated_at"}
        }
        merged.update(changes.model_dump(exclude_unset=True, mode="json"))
        try:
            validated = VPNConnectionCreate.model_validate(merged)
        except ValidationError as exc:
            raise VPNRegistryError("connection_patch_invalid") from exc
        tenant = self._validate_references(validated)
        try:
            return self.store.update(validated, tenant["slug"])
        except VPNConnectionNotFound:
            raise VPNRegistryError("connection_not_found", 404) from None
        except VPNStoreError:
            raise VPNRegistryError("vpn_registry_unavailable", 503) from None

    def deactivate(self, connection_id: str) -> VPNConnectionRecord:
        return self.patch(
            connection_id,
            VPNConnectionPatch(administrative_status="disabled"),
        )

    def validate(self, connection_id: str) -> dict:
        connection = self.get(connection_id)
        self._validate_references(
            VPNConnectionCreate.model_validate(
                connection.model_dump(
                    exclude={"id", "tenant_slug", "runtime_status", "created_at", "updated_at"}
                )
            )
        )
        try:
            metadata = self.openbao.read_secret_metadata(connection.secret_ref)
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                raise VPNRegistryError("vpn_secret_not_found", 404) from None
            raise VPNRegistryError("vpn_secret_validation_unavailable", 503) from None
        current_version = (
            metadata.get("current_version") if isinstance(metadata, dict) else None
        )
        versions = metadata.get("versions") if isinstance(metadata, dict) else None
        version = versions.get(str(current_version)) if isinstance(versions, dict) else None
        if (
            isinstance(current_version, bool)
            or not isinstance(current_version, int)
            or current_version <= 0
            or not isinstance(version, dict)
        ):
            raise VPNRegistryError("vpn_secret_validation_unavailable", 503)
        if version.get("destroyed") is True or version.get("deletion_time"):
            raise VPNRegistryError("vpn_secret_not_found", 404)
        return {
            "connection_id": connection.connection_id,
            "tenant_id": connection.tenant_id,
            "management_vrf_id": connection.management_vrf_id,
            "secret_available": True,
            "administrative_status": connection.administrative_status,
            "runtime_status": "not_deployed",
            "validation_status": "valid",
        }

    def resolve_device(
        self, device_id: int, *, expected_tenant: int | str | None = None
    ) -> dict:
        device = self._one(
            "/api/dcim/devices/", {"id": device_id},
            "device_not_found", "device_ambiguous",
        )
        tenant_id = related_id(device.get("tenant"))
        site_id = related_id(device.get("site"))
        if tenant_id is None:
            raise VPNRegistryError("device_tenant_missing")
        if site_id is None:
            raise VPNRegistryError("device_site_missing")
        tenant = self._tenant(tenant_id)
        if expected_tenant is not None and str(expected_tenant) not in {
            str(tenant_id), tenant["slug"],
        }:
            raise VPNRegistryError("device_outside_requested_tenant", 403)
        site = self._one(
            "/api/dcim/sites/", {"id": site_id},
            "site_not_found", "site_ambiguous",
        )
        if related_id(site.get("tenant")) != tenant_id:
            raise VPNRegistryError("site_tenant_mismatch", 409)

        primary_id = related_id(device.get("primary_ip4"))
        if primary_id is None:
            raise VPNRegistryError("management_ip_missing")
        address = self._one(
            "/api/ipam/ip-addresses/", {"id": primary_id},
            "management_ip_not_found", "management_ip_ambiguous",
        )
        if related_id(address.get("tenant")) != tenant_id:
            raise VPNRegistryError("management_ip_tenant_mismatch", 409)
        vrf_id = related_id(address.get("vrf"))
        if vrf_id is None:
            raise VPNRegistryError("management_vrf_missing")
        vrf = self._one(
            "/api/ipam/vrfs/", {"id": vrf_id},
            "management_vrf_not_found", "management_vrf_ambiguous",
        )
        if related_id(vrf.get("tenant")) != tenant_id:
            raise VPNRegistryError("management_vrf_tenant_mismatch", 409)
        try:
            target = ip_interface(str(address["address"])).ip
        except (KeyError, ValueError):
            raise VPNRegistryError("management_ip_invalid") from None

        device_connection = _custom_field(
            device.get("custom_fields"), "atlas_connection_id"
        )
        site_connection = _custom_field(
            site.get("custom_fields"), "atlas_connection_id"
        )
        connection_id = device_connection or site_connection
        source = "device" if device_connection else "site" if site_connection else None
        if connection_id is None:
            return {
                "device_id": device_id,
                "device_name": device.get("name"),
                "tenant_id": tenant_id,
                "tenant_slug": tenant["slug"],
                "site_id": site_id,
                "management_ip": str(target),
                "vrf_id": vrf_id,
                "connection_id": None,
                "connection_source": None,
                "runtime_status": "not_deployed",
                "resolution_status": "unmapped",
            }
        connection = self.get(connection_id)
        if connection.tenant_id != tenant_id:
            raise VPNRegistryError("connection_tenant_mismatch", 409)
        if connection.management_vrf_id != vrf_id:
            raise VPNRegistryError("connection_vrf_mismatch", 409)
        if not any(
            target in ip_network(prefix, strict=True)
            for prefix in connection.allowed_prefixes
        ):
            raise VPNRegistryError("management_ip_outside_allowed_prefixes", 409)
        return {
            "device_id": device_id,
            "device_name": device.get("name"),
            "tenant_id": tenant_id,
            "tenant_slug": tenant["slug"],
            "site_id": site_id,
            "management_ip": str(target),
            "vrf_id": vrf_id,
            "connection_id": connection.connection_id,
            "connection_source": source,
            "administrative_status": connection.administrative_status,
            "runtime_status": "not_deployed",
            "resolution_status": "resolved",
            "vendor": connection.vendor,
            "protocol": connection.protocol,
            "driver": connection.driver,
            "gateway_host": connection.gateway_host,
            "gateway_port": connection.gateway_port,
        }


__all__ = ["VPNRegistryError", "VPNRegistryService"]
