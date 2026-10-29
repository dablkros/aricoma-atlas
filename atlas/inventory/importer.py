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
    def __init__(
        self,
        client,
        config: dict,
        *,
        planned_device_types: list[dict] | None = None,
        device_type_errors: dict[str, str] | None = None,
    ):
        self.client = client
        self.config = config
        self._device_type_cache: list[dict] | None = None
        self._planned_device_types = planned_device_types or []
        self._device_type_errors = device_type_errors or {}

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
        group: str | None = None,
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
            if group:
                groups = self._all("/api/tenancy/tenant-groups/", {"slug": slugify(group)})
                if len(groups) != 1:
                    raise InventoryInvalid(
                        "tenant_group_not_found" if not groups else "tenant_group_ambiguous"
                    )
                if related_id(tenant.get("group")) != groups[0].get("id"):
                    raise InventoryConflict("tenant_group_conflict")
            return tenant, "UNCHANGED"
        if not apply:
            return None, "CREATE"
        tenant_group = self.ensure_tenant_group(group, apply=True)[0] if group else None
        payload = {"name": clean_name, "slug": tenant_slug}
        if tenant_group is not None:
            payload["group"] = tenant_group["id"]
        return self.client.post(
            "/api/tenancy/tenants/",
            payload,
        ), "CREATE"

    def ensure_tenant_group(
        self, name: str, *, apply: bool
    ) -> tuple[dict | None, str]:
        clean_name = " ".join(name.split())
        group_slug = slugify(clean_name)
        if not group_slug:
            raise InventoryInvalid("tenant_group_name_invalid")
        matches = self._all("/api/tenancy/tenant-groups/", {"slug": group_slug})
        if len(matches) > 1:
            raise InventoryConflict("tenant_group_ambiguous")
        if matches:
            current = matches[0]
            if normalized_name(str(current.get("name", ""))) != normalized_name(clean_name):
                raise InventoryConflict("tenant_group_identity_conflict")
            return current, "UNCHANGED"
        if not apply:
            return None, "CREATE"
        return self.client.post(
            "/api/tenancy/tenant-groups/", {"name": clean_name, "slug": group_slug},
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
        tenant_slug = str(tenant.get("slug", "")).strip()
        local_slug = slugify(clean_name)
        site_slug = slugify(f"{tenant_slug}-{local_slug}")
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
        owned_name_matches = [
            site for site in name_matches
            if related_id(site.get("tenant")) == tenant.get("id")
        ]
        if len(owned_name_matches) == 1:
            return owned_name_matches[0], "UNCHANGED"
        if len(owned_name_matches) > 1:
            raise InventoryConflict(
                "site_name_ambiguous"
            )
        if not apply:
            return None, "CREATE"
        return self.client.post(
            "/api/dcim/sites/",
            {"name": clean_name, "slug": site_slug, "tenant": tenant["id"], "status": "active"},
        ), "CREATE"

    def ensure_vrf(
        self,
        tenant: dict,
        name: str,
        *,
        apply: bool,
        enforce_unique: bool = True,
    ) -> tuple[dict | None, str]:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise InventoryInvalid("vrf_name_invalid")
        matches = [
            item
            for item in self._all("/api/ipam/vrfs/", {"name": clean_name})
            if normalized_name(str(item.get("name", ""))) == normalized_name(clean_name)
        ]
        owned = [
            item for item in matches
            if related_id(item.get("tenant")) == tenant.get("id")
        ]
        if len(owned) > 1:
            raise InventoryConflict("vrf_ambiguous")
        if owned:
            current = owned[0]
            if bool(current.get("enforce_unique")) != enforce_unique:
                raise InventoryConflict("vrf_enforce_unique_conflict")
            return current, "UNCHANGED"
        if matches:
            raise InventoryConflict("vrf_belongs_to_different_tenant")
        if not apply:
            return None, "CREATE"
        return self.client.post(
            "/api/ipam/vrfs/",
            {
                "name": clean_name,
                "tenant": tenant["id"],
                "enforce_unique": enforce_unique,
            },
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
            self._device_type_cache = [
                *self._all("/api/dcim/device-types/"),
                *self._planned_device_types,
            ]
        matches = []
        for item in self._device_type_cache:
            manufacturer = item.get("manufacturer")
            manufacturer_name = (
                str(manufacturer.get("name", ""))
                if isinstance(manufacturer, dict)
                else ""
            )
            model_name = str(item.get("model", ""))
            identifiers = {
                normalized_name(model_name),
                normalized_name(str(item.get("slug", ""))),
                normalized_name(str(item.get("part_number", ""))),
                normalized_name(f"{manufacturer_name} {model_name}"),
            }
            if target in identifiers:
                matches.append(item)
        if not matches:
            raise InventoryInvalid(
                self._device_type_errors.get(target, "device_type_not_found")
            )
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

    def _resolve_vrf(self, value: str | None, tenant: dict) -> dict | None:
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
        vrf = matches[0]
        if related_id(vrf.get("tenant")) != tenant.get("id"):
            raise InventoryConflict("vrf_belongs_to_different_tenant")
        return vrf

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

    def _validate_planned_management(
        self, address: str, vrf: dict | None, tenant: dict
    ) -> None:
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
        ip_tenant = related_id(existing.get("tenant"))
        if ip_tenant not in (None, tenant.get("id")):
            raise InventoryConflict("management_ip_belongs_to_different_tenant")
        if existing.get("address") != address:
            raise InventoryConflict("management_ip_prefix_conflict")
        assigned_id = existing.get("assigned_object_id") or related_id(
            existing.get("assigned_object")
        )
        if assigned_id is not None:
            raise InventoryConflict("management_ip_assigned_elsewhere")

    def _ensure_management_prefix(
        self,
        address: str,
        vrf: dict | None,
        tenant: dict,
        *,
        apply: bool,
    ) -> dict | None:
        interface = ipaddress.ip_interface(address)
        if interface.network.prefixlen == interface.max_prefixlen:
            return None
        prefix = str(interface.network)
        vrf_id = vrf["id"] if vrf else None
        matches = self._all("/api/ipam/prefixes/", {"prefix": prefix})
        contextual = [item for item in matches if related_id(item.get("vrf")) == vrf_id]
        if len(contextual) > 1:
            raise InventoryConflict("management_prefix_ambiguous_in_vrf")
        if contextual:
            current = contextual[0]
            prefix_tenant = related_id(current.get("tenant"))
            if prefix_tenant not in (None, tenant.get("id")):
                raise InventoryConflict("management_prefix_belongs_to_different_tenant")
            return current
        if not apply:
            return None
        payload = {"prefix": prefix, "status": "active", "tenant": tenant["id"]}
        if vrf_id is not None:
            payload["vrf"] = vrf_id
        return self.client.post("/api/ipam/prefixes/", payload)

    def _management_state(
        self,
        device: dict,
        address: str,
        vrf: dict | None,
        tenant: dict,
        *,
        apply: bool,
    ):
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
            ip_tenant = related_id(ip_object.get("tenant"))
            if ip_tenant not in (None, tenant.get("id")):
                raise InventoryConflict("management_ip_belongs_to_different_tenant")
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
                    "tenant": tenant["id"],
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
        default_site = self.config.get("default_site")
        for row in rows:
            tenant_label = tenant_override or row.tenant or (
                default_tenant.get("slug") if isinstance(default_tenant, dict) else None
            )
            site_label = row.site or site_override or (
                default_site.get("slug") if isinstance(default_site, dict) else None
            )
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
                device_type_id = device_type.get("id")
                device_type_planned = (
                    isinstance(device_type_id, int)
                    and not isinstance(device_type_id, bool)
                    and device_type_id < 0
                )
                platform = self._resolve_platform(row, device_type)
                role = self._resolve_role(row, device_type, platform)
                vrf = self._resolve_vrf(row.vrf or vrf_override, tenant)
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
                    self._ensure_management_prefix(
                        address, vrf, tenant, apply=False,
                    )
                    self._validate_planned_management(address, vrf, tenant)
                    operation = (
                        "CREATE_DEVICE_TYPE_DEVICE_AND_MANAGEMENT"
                        if device_type_planned
                        else "CREATE_DEVICE_AND_MANAGEMENT"
                    )
                    results.append(RowResult(
                        **base,
                        operation=operation,
                        status=ImportStatus.CREATE,
                    ))
                    continue
                self._ensure_management_prefix(
                    address, vrf, tenant, apply=apply,
                )
                changed, ids = self._management_state(
                    device, address, vrf, tenant, apply=apply
                )
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
