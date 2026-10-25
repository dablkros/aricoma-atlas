"""Plan and apply tenant-aware NetBox device imports."""

import ipaddress
import re
import unicodedata
from typing import Any

from atlas.inventory.models import (
    ImportReport,
    ImportStatus,
    InventoryRow,
    RowResult,
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")[:100]


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def related_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        identifier = value.get("id")
        return identifier if isinstance(identifier, int) and not isinstance(identifier, bool) else None
    return None


class InventoryConflict(ValueError):
    pass


class InventoryInvalid(ValueError):
    pass


class InventoryImporter:
    def __init__(self, client, config: dict):
        self.client = client
        self.config = config
        self._device_type_cache: list[dict] | None = None

    def _all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        values = self.client.get_all(endpoint, params=params)
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise RuntimeError("NetBox returned an invalid object list")
        return values

    def ensure_tenant(
        self,
        name: str,
        *,
        apply: bool,
        slug: str | None = None,
    ) -> tuple[dict | None, str]:
        clean_name = " ".join(name.split())
        tenant_slug = slug or slugify(clean_name)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tenant_slug):
            raise InventoryInvalid("tenant_name_invalid")
        matches = self._all("/api/tenancy/tenants/", {"slug": tenant_slug})
        if len(matches) > 1:
            raise InventoryConflict("tenant_slug_ambiguous")
        if matches:
            tenant = matches[0]
            if normalized_name(str(tenant.get("name", ""))) != normalized_name(clean_name):
                raise InventoryConflict("tenant_slug_identity_conflict")
            return tenant, "UNCHANGED"
        if not apply:
            return None, "CREATE"
        return self.client.post(
            "/api/tenancy/tenants/",
            {"name": clean_name, "slug": tenant_slug},
        ), "CREATE"

    def resolve_tenant(self, value: str) -> dict:
        clean = " ".join(value.split())
        candidate_slug = clean if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", clean) else slugify(clean)
        matches = self._all("/api/tenancy/tenants/", {"slug": candidate_slug})
        if len(matches) != 1:
            raise InventoryInvalid("tenant_not_found" if not matches else "tenant_ambiguous")
        tenant = matches[0]
        if clean != candidate_slug and normalized_name(str(tenant.get("name", ""))) != normalized_name(clean):
            raise InventoryConflict("tenant_identity_conflict")
        return tenant

    def ensure_site(
        self,
        tenant: dict,
        name: str,
        *,
        apply: bool,
    ) -> tuple[dict | None, str]:
        clean_name = " ".join(name.split())
        site_slug = slugify(clean_name)
        if not site_slug:
            raise InventoryInvalid("site_name_invalid")
        matches = self._all("/api/dcim/sites/", {"slug": site_slug})
        if len(matches) > 1:
            raise InventoryConflict("site_slug_ambiguous")
        if matches:
            site = matches[0]
            if normalized_name(str(site.get("name", ""))) != normalized_name(clean_name):
                raise InventoryConflict("site_slug_identity_conflict")
            if related_id(site.get("tenant")) != tenant.get("id"):
                raise InventoryConflict("site_belongs_to_different_tenant")
            return site, "UNCHANGED"
        name_matches = [
            site for site in self._all("/api/dcim/sites/", {"name": clean_name})
            if normalized_name(str(site.get("name", ""))) == normalized_name(clean_name)
        ]
        if name_matches:
            raise InventoryConflict(
                "site_name_ambiguous" if len(name_matches) > 1 else "site_name_slug_conflict"
            )
        if not apply:
            return None, "CREATE"
        return self.client.post(
            "/api/dcim/sites/",
            {"name": clean_name, "slug": site_slug, "tenant": tenant["id"], "status": "active"},
        ), "CREATE"

    def _resolve_site(self, tenant: dict, value: str) -> dict:
        clean = " ".join(value.split())
        tenant_id = tenant["id"]
        by_slug = self._all("/api/dcim/sites/", {"slug": clean}) if re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", clean
        ) else []
        matches = by_slug or [
            site for site in self._all("/api/dcim/sites/", {"name": clean})
            if normalized_name(str(site.get("name", ""))) == normalized_name(clean)
        ]
        owned = [site for site in matches if related_id(site.get("tenant")) == tenant_id]
        if not matches:
            raise InventoryInvalid("site_not_found")
        if not owned:
            raise InventoryConflict("site_belongs_to_different_tenant")
        if len(owned) != 1:
            raise InventoryConflict("site_ambiguous")
        return owned[0]

    def _resolve_device_type(self, model: str) -> dict:
        requested = normalized_name(model)
        alias = self.config["device_type_aliases"].get(model)
        if alias is None:
            alias = next((
                value for key, value in self.config["device_type_aliases"].items()
                if normalized_name(key) == requested
            ), None)
        target = normalized_name(alias or model)
        if self._device_type_cache is None:
            self._device_type_cache = self._all("/api/dcim/device-types/")
        matches = [
            item for item in self._device_type_cache
            if normalized_name(str(item.get("model", ""))) == target
        ]
        if not matches:
            raise InventoryInvalid("device_type_not_found")
        if len(matches) != 1:
            raise InventoryConflict("device_type_ambiguous")
        return matches[0]

    def _resolve_platform(self, row: InventoryRow, device_type: dict) -> dict | None:
        platform = device_type.get("default_platform")
        platform_id = related_id(platform)
        if row.platform:
            matches = self._all("/api/dcim/platforms/", {"slug": row.platform})
            if len(matches) != 1:
                raise InventoryInvalid("platform_not_found_or_ambiguous")
            if platform_id != matches[0].get("id"):
                raise InventoryConflict("platform_not_verified_for_device_type")
            return matches[0]
        return platform if isinstance(platform, dict) and platform_id is not None else None

    def _resolve_role(self, row: InventoryRow, device_type: dict, platform: dict | None) -> dict:
        model = str(device_type.get("model", ""))
        platform_slug = str((platform or {}).get("slug", ""))
        name = (
            row.device_role
            or self.config["role_mappings"]["models"].get(model)
            or self.config["role_mappings"]["platforms"].get(platform_slug)
            or self.config.get("default_device_role")
        )
        if not name:
            raise InventoryInvalid("device_role_not_selected")
        candidates = self._all("/api/dcim/device-roles/", {"slug": name})
        if not candidates:
            candidates = [
                item for item in self._all("/api/dcim/device-roles/", {"name": name})
                if normalized_name(str(item.get("name", ""))) == normalized_name(name)
            ]
        if len(candidates) != 1:
            raise InventoryInvalid("device_role_not_found" if not candidates else "device_role_ambiguous")
        return candidates[0]

    def _resolve_vrf(self, value: str | None) -> dict | None:
        if not value:
            return None
        clean = value.strip()
        if clean.isdigit():
            matches = self._all("/api/ipam/vrfs/", {"id": int(clean)})
        else:
            matches = self._all("/api/ipam/vrfs/", {"name": clean})
            if not matches:
                matches = self._all("/api/ipam/vrfs/", {"rd": clean})
            matches = [
                item for item in matches
                if normalized_name(str(item.get("name", ""))) == normalized_name(clean)
                or str(item.get("rd", "")) == clean
            ]
        if len(matches) != 1:
            raise InventoryInvalid("vrf_not_found" if not matches else "vrf_ambiguous")
        return matches[0]

    def _address(self, value: str) -> str:
        candidate = value.strip()
        if "/" not in candidate:
            candidate += f"/{self.config['default_ipv4_prefix_length']}"
        try:
            address = ipaddress.ip_interface(candidate)
        except ValueError:
            raise InventoryInvalid("management_ip_invalid") from None
        if address.version != 4:
            raise InventoryInvalid("management_ip_not_ipv4")
        return str(address)

    @staticmethod
    def _hostname(value: str) -> str:
        hostname = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,253}[A-Za-z0-9])?", hostname):
            raise InventoryInvalid("hostname_invalid")
        return hostname

    def _existing_device(self, hostname: str, tenant: dict, site: dict) -> dict | None:
        matches = self._all("/api/dcim/devices/", {"name": hostname})
        contextual = [
            item for item in matches
            if related_id(item.get("tenant")) == tenant["id"]
            and related_id(item.get("site")) == site["id"]
        ]
        if len(contextual) > 1:
            raise InventoryConflict("device_identity_ambiguous")
        return contextual[0] if contextual else None

    def _device_payload(self, hostname, tenant, site, device_type, role, platform):
        payload: dict[str, Any] = {
            "name": hostname,
            "tenant": tenant["id"],
            "site": site["id"],
            "device_type": device_type["id"],
            "role": role["id"],
            "status": "active",
            "custom_fields": {
                "oxidized_enabled": False,
                "monitoring_enabled": False,
                "profylaxia_enabled": False,
                "profylaxia_checks": [],
            },
        }
        if platform is not None:
            payload["platform"] = platform["id"]
        return payload

    @staticmethod
    def _device_conflict(device: dict, payload: dict) -> str | None:
        for field in ("tenant", "site", "device_type", "role", "platform"):
            if field in payload and related_id(device.get(field)) != payload[field]:
                return f"existing_device_{field}_conflict"
        status = device.get("status")
        status_value = status.get("value") if isinstance(status, dict) else status
        if status_value not in (None, "active"):
            return "existing_device_status_conflict"
        return None

    def _validate_planned_management(self, address: str, vrf: dict | None) -> None:
        """Validate a new device IP in dry-run without creating placeholder objects."""
        host = str(ipaddress.ip_interface(address).ip)
        vrf_id = vrf["id"] if vrf else None
        matches = [
            item for item in self._all("/api/ipam/ip-addresses/", {"address": host})
            if related_id(item.get("vrf")) == vrf_id
        ]
        if len(matches) > 1:
            raise InventoryConflict("management_ip_ambiguous_in_vrf")
        if not matches:
            return
        existing = matches[0]
        if existing.get("address") != address:
            raise InventoryConflict("management_ip_prefix_conflict")
        assigned_id = existing.get("assigned_object_id") or related_id(
            existing.get("assigned_object")
        )
        if assigned_id is not None:
            raise InventoryConflict("management_ip_assigned_elsewhere")

    def _management_state(self, device: dict, address: str, vrf: dict | None, *, apply: bool):
        interface_name = self.config["management_interface"]
        interfaces = self._all(
            "/api/dcim/interfaces/", {"device_id": device["id"], "name": interface_name}
        )
        if len(interfaces) > 1:
            raise InventoryConflict("management_interface_ambiguous")
        changed = False
        interface = interfaces[0] if interfaces else None
        if interface is None:
            changed = True
            if apply:
                interface = self.client.post("/api/dcim/interfaces/", {
                    "device": device["id"], "name": interface_name,
                    "type": "virtual", "mgmt_only": True, "enabled": True,
                })

        host = str(ipaddress.ip_interface(address).ip)
        addresses = self._all("/api/ipam/ip-addresses/", {"address": host})
        vrf_id = vrf["id"] if vrf else None
        contextual = [item for item in addresses if related_id(item.get("vrf")) == vrf_id]
        if len(contextual) > 1:
            raise InventoryConflict("management_ip_ambiguous_in_vrf")
        ip_object = contextual[0] if contextual else None
        if ip_object is not None:
            assigned_type = ip_object.get("assigned_object_type")
            assigned_id = ip_object.get("assigned_object_id") or related_id(ip_object.get("assigned_object"))
            if assigned_id is not None and (assigned_type != "dcim.interface" or not interface or assigned_id != interface["id"]):
                raise InventoryConflict("management_ip_assigned_elsewhere")
            stored = ip_object.get("address")
            if isinstance(stored, str) and stored != address:
                raise InventoryConflict("management_ip_prefix_conflict")
        else:
            changed = True
            if apply:
                if interface is None:
                    raise RuntimeError("management interface was not created")
                payload = {
                    "address": address,
                    "status": "active",
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface["id"],
                }
                if vrf_id is not None:
                    payload["vrf"] = vrf_id
                ip_object = self.client.post("/api/ipam/ip-addresses/", payload)
        if ip_object is not None and interface is not None:
            assigned_id = ip_object.get("assigned_object_id") or related_id(ip_object.get("assigned_object"))
            if assigned_id is None:
                changed = True
                if apply:
                    ip_object = self.client.patch(
                        f"/api/ipam/ip-addresses/{ip_object['id']}/",
                        {"assigned_object_type": "dcim.interface", "assigned_object_id": interface["id"]},
                    )
            if related_id(device.get("primary_ip4")) != ip_object["id"]:
                changed = True
                if apply:
                    self.client.patch(
                        f"/api/dcim/devices/{device['id']}/", {"primary_ip4": ip_object["id"]}
                    )
        ids = {"device": device["id"]}
        if interface is not None:
            ids["interface"] = interface["id"]
        if ip_object is not None:
            ids["ip_address"] = ip_object["id"]
        return changed, ids

    def import_rows(
        self,
        rows: list[InventoryRow],
        *,
        tenant_override: str | None = None,
        site_override: str | None = None,
        vrf_override: str | None = None,
        apply: bool = False,
    ) -> ImportReport:
        results: list[RowResult] = []
        seen: set[tuple[str, str, str]] = set()
        default_tenant = self.config.get("default_tenant")
        for row in rows:
            tenant_label = tenant_override or row.tenant or (
                default_tenant.get("slug") if isinstance(default_tenant, dict) else None
            )
            site_label = row.site or site_override
            base = dict(
                row=row.number, hostname=row.hostname, model=row.model,
                management_ip=row.management_ip, tenant=tenant_label,
                site=site_label,
            )
            try:
                if tenant_override and row.tenant:
                    row_tenant = self.resolve_tenant(row.tenant)
                    cli_tenant = self.resolve_tenant(tenant_override)
                    if row_tenant["id"] != cli_tenant["id"]:
                        raise InventoryConflict("row_tenant_conflicts_with_cli")
                    tenant = cli_tenant
                elif tenant_label:
                    tenant = self.resolve_tenant(tenant_label)
                else:
                    raise InventoryInvalid("tenant_not_selected")
                if not site_label:
                    raise InventoryInvalid("site_not_selected")
                site = self._resolve_site(tenant, site_label)
                if site_override and row.site:
                    cli_site = self._resolve_site(tenant, site_override)
                    if cli_site["id"] != site["id"]:
                        raise InventoryConflict("row_site_conflicts_with_cli")
                hostname = self._hostname(row.hostname)
                address = self._address(row.management_ip)
                duplicate_key = (hostname.casefold(), str(tenant["id"]), str(site["id"]))
                if duplicate_key in seen:
                    raise InventoryInvalid("duplicate_input_row")
                seen.add(duplicate_key)
                device_type = self._resolve_device_type(row.model)
                platform = self._resolve_platform(row, device_type)
                role = self._resolve_role(row, device_type, platform)
                vrf = self._resolve_vrf(row.vrf or vrf_override)
                payload = self._device_payload(hostname, tenant, site, device_type, role, platform)
                device = self._existing_device(hostname, tenant, site)
                created = device is None
                if device is not None:
                    conflict = self._device_conflict(device, payload)
                    if conflict:
                        raise InventoryConflict(conflict)
                elif apply:
                    device = self.client.post("/api/dcim/devices/", payload)
                if device is None:
                    self._validate_planned_management(address, vrf)
                    results.append(RowResult(**base, operation="CREATE_DEVICE_AND_MANAGEMENT", status=ImportStatus.CREATE))
                    continue
                changed, ids = self._management_state(device, address, vrf, apply=apply)
                status = ImportStatus.CREATE if created or changed else ImportStatus.UNCHANGED
                results.append(RowResult(
                    **base,
                    operation="CREATE" if status == ImportStatus.CREATE else "UNCHANGED",
                    status=status,
                    object_ids=ids,
                ))
            except InventoryInvalid as exc:
                results.append(RowResult(
                    **base, operation="NONE", status=ImportStatus.INVALID, reason=str(exc)
                ))
            except InventoryConflict as exc:
                results.append(RowResult(
                    **base, operation="NONE", status=ImportStatus.CONFLICT, reason=str(exc)
                ))
            except Exception as exc:
                results.append(RowResult(
                    **base, operation="NONE", status=ImportStatus.ERROR,
                    reason=f"{type(exc).__name__}: {str(exc)[:240]}",
                ))
        return ImportReport(mode="APPLY" if apply else "DRY_RUN", rows=results)
