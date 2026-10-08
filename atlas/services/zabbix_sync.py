"""NetBox desired-state reconciliation into Atlas-owned Zabbix hosts."""

import ipaddress
import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional, Tuple

from atlas.device_credentials import DeviceValidationError
from atlas.integrations.zabbix import ZabbixAPIError
from atlas.services.errors import DependencyError
from atlas.services.zabbix import (
    MANAGED_BY_TAG,
    MANAGED_BY_VALUE,
    NETBOX_ID_TAG,
    ZabbixService,
    translate_zabbix_error,
)
from atlas.zabbix_config import load_zabbix_config


logger = logging.getLogger("atlas.zabbix.sync")


class ZabbixSyncFailure(RuntimeError):
    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class ZabbixSyncIssue:
    device: Optional[str]
    code: str


@dataclass(frozen=True)
class ZabbixSyncSummary:
    total: int
    created: int
    updated: int
    disabled: int
    unchanged: int
    errors: int


@dataclass(frozen=True)
class ZabbixSyncResult:
    status: str
    summary: ZabbixSyncSummary
    created: Tuple[str, ...]
    updated: Tuple[str, ...]
    disabled: Tuple[str, ...]
    unchanged: Tuple[str, ...]
    issues: Tuple[ZabbixSyncIssue, ...]


@dataclass(frozen=True)
class DesiredHost:
    netbox_id: int
    name: str
    address: str
    platform: str
    vendor: str
    profile: str
    site: str
    template_name: str
    secret_version: int
    community: str = field(repr=False)


class SyncLock:
    def __init__(self) -> None:
        self._lock = Lock()

    def acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()


def _nested_slug(value) -> Optional[str]:
    return value.get("slug") if isinstance(value, dict) else None


def desired_host(device: dict, openbao, config: dict) -> DesiredHost:
    fields = device.get("custom_fields")
    if not isinstance(fields, dict) or fields.get("monitoring_enabled") is not True:
        raise DeviceValidationError(
            "Invalid monitoring flag",
            code="invalid_monitoring_enabled",
        )
    identifier = device.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise DeviceValidationError("Missing NetBox device id", code="missing_device_id")
    name = device.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DeviceValidationError("Missing device name", code="missing_name")
    platform = _nested_slug(device.get("platform"))
    if not platform:
        raise DeviceValidationError("Missing platform", code="missing_platform")
    template_name = config["platform_templates"].get(platform)
    if not template_name:
        raise DeviceValidationError(
            "Unsupported monitoring platform",
            code="unsupported_platform",
        )
    device_type = device.get("device_type")
    vendor = _nested_slug(
        device_type.get("manufacturer") if isinstance(device_type, dict) else None
    )
    if not vendor:
        raise DeviceValidationError("Missing manufacturer", code="missing_manufacturer")
    profile = fields.get("credential_profile")
    if not isinstance(profile, str) or not profile:
        raise DeviceValidationError(
            "Missing credential profile",
            code="missing_credential_profile",
        )
    primary = device.get("primary_ip4") or device.get("primary_ip6") or device.get("primary_ip")
    address_value = primary.get("address") if isinstance(primary, dict) else None
    try:
        address = str(ipaddress.ip_interface(address_value).ip)
    except (TypeError, ValueError):
        raise DeviceValidationError(
            "Missing primary IP",
            code="missing_primary_ip",
        ) from None
    credentials, secret_version = openbao.get_snmp_credentials(vendor, profile)
    if credentials.get("version") != "2c":
        raise DeviceValidationError(
            "SNMP version is not enabled by the first Zabbix release",
            code="unsupported_snmp_version",
            category="error",
        )
    site = _nested_slug(device.get("site")) or ""
    return DesiredHost(
        identifier,
        name.strip(),
        address,
        platform,
        vendor,
        profile,
        site,
        template_name,
        secret_version,
        credentials["community"],
    )


class ZabbixSyncService:
    def __init__(
        self,
        netbox,
        openbao,
        zabbix: ZabbixService,
        config=None,
        sync_lock: Optional[SyncLock] = None,
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.zabbix = zabbix
        self.config = config or load_zabbix_config()
        self.sync_lock = sync_lock or SyncLock()

    @staticmethod
    def _tags(host: dict) -> dict:
        tags = host.get("tags")
        if not isinstance(tags, list):
            return {}
        return {
            item.get("tag"): item.get("value")
            for item in tags
            if isinstance(item, dict) and isinstance(item.get("tag"), str)
        }

    @staticmethod
    def _label(device: dict) -> str:
        name = device.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        identifier = device.get("id")
        return f"id:{identifier}" if identifier is not None else "<unnamed>"

    @staticmethod
    def _log_change(action: str, device: str) -> None:
        logger.info(
            "zabbix_sync_change_planned",
            extra={
                "event": "zabbix_sync_change_planned",
                "action": action,
                "device": device,
            },
        )

    @staticmethod
    def _managed_host_params() -> dict:
        return {
            "output": ["hostid", "host", "name", "status"],
            "selectHostGroups": ["groupid", "name"],
            "selectInterfaces": "extend",
            "selectParentTemplates": ["templateid", "host", "name"],
            "selectTags": "extend",
            "tags": [
                {
                    "tag": MANAGED_BY_TAG,
                    "value": MANAGED_BY_VALUE,
                    "operator": 1,
                }
            ],
        }

    def _load_remote(self, client):
        try:
            groups = client.hostgroup_get(
                {"output": ["groupid", "name"], "filter": {"name": [self.config["managed_host_group"]]}}
            )
            if len(groups) != 1:
                raise ZabbixSyncFailure("managed_host_group_missing")
            template_names = sorted(set(self.config["platform_templates"].values()))
            templates = client.template_get(
                {
                    "output": ["templateid", "host", "name"],
                    "filter": {"host": template_names},
                }
            )
            by_name = {str(item.get("host")): item for item in templates}
            hosts = client.host_get(self._managed_host_params())
            return groups[0]["groupid"], by_name, hosts
        except ZabbixAPIError as exc:
            raise ZabbixSyncFailure(translate_zabbix_error(exc).reason) from exc

    @staticmethod
    def _desired_tags(item: DesiredHost) -> list[dict]:
        values = {
            MANAGED_BY_TAG: MANAGED_BY_VALUE,
            NETBOX_ID_TAG: str(item.netbox_id),
            "atlas_platform": item.platform,
            "atlas_snmp_secret_version": str(item.secret_version),
        }
        if item.site:
            values["atlas_site"] = item.site
        return [{"tag": key, "value": values[key]} for key in sorted(values)]

    @classmethod
    def _payload(cls, item: DesiredHost, groupid: str, templateid: str) -> dict:
        return {
            "host": item.name,
            "name": item.name,
            "status": 0,
            "groups": [{"groupid": str(groupid)}],
            "templates": [{"templateid": str(templateid)}],
            "interfaces": [
                {
                    "type": 2,
                    "main": 1,
                    "useip": 1,
                    "ip": item.address,
                    "dns": "",
                    "port": "161",
                    "details": {
                        "version": 2,
                        "bulk": 1,
                        "community": "{$SNMP_COMMUNITY}",
                    },
                }
            ],
            "macros": [
                {
                    "macro": "{$SNMP_COMMUNITY}",
                    "value": item.community,
                    "type": 1,
                }
            ],
            "tags": cls._desired_tags(item),
        }

    @classmethod
    def _matches(
        cls,
        host: dict,
        item: DesiredHost,
        groupid: str,
        templateid: str,
    ) -> bool:
        interfaces = host.get("interfaces")
        snmp = [
            interface for interface in interfaces or []
            if isinstance(interface, dict) and str(interface.get("type")) == "2"
        ]
        template_ids = {
            str(template.get("templateid"))
            for template in host.get("parentTemplates") or []
            if isinstance(template, dict)
        }
        group_ids = {
            str(group.get("groupid"))
            for group in host.get("hostgroups") or []
            if isinstance(group, dict)
        }
        details = snmp[0].get("details") if len(snmp) == 1 else {}
        details = details if isinstance(details, dict) else {}
        wanted_tags = {entry["tag"]: entry["value"] for entry in cls._desired_tags(item)}
        tags = cls._tags(host)
        return (
            str(host.get("host")) == item.name
            and str(host.get("name")) == item.name
            and str(host.get("status")) == "0"
            and len(snmp) == 1
            and str(snmp[0].get("ip")) == item.address
            and str(snmp[0].get("port")) == "161"
            and str(details.get("version")) == "2"
            and str(details.get("bulk")) == "1"
            and details.get("community") == "{$SNMP_COMMUNITY}"
            and group_ids == {str(groupid)}
            and template_ids == {str(templateid)}
            and all(tags.get(key) == value for key, value in wanted_tags.items())
        )

    def sync(self) -> ZabbixSyncResult:
        if not self.sync_lock.acquire():
            raise ZabbixSyncFailure("sync_already_running", status_code=409)
        logger.info("zabbix_sync_started", extra={"event": "zabbix_sync_started"})
        try:
            try:
                devices = self.netbox.get_monitoring_devices()
                client = self.zabbix.client()
                groupid, templates, current = self._load_remote(client)
            except DependencyError as exc:
                raise ZabbixSyncFailure(exc.reason) from exc

            current_by_id = {}
            issues = []
            protected_ids = set()
            for host in current:
                value = self._tags(host).get(NETBOX_ID_TAG)
                if not isinstance(value, str) or not value.isdigit():
                    issues.append(
                        ZabbixSyncIssue(str(host.get("host") or "<unknown>"), "invalid_netbox_id_tag")
                    )
                    continue
                if value in current_by_id:
                    raise ZabbixSyncFailure("ambiguous_host_ownership", status_code=409)
                current_by_id[value] = host

            created = []
            updated = []
            disabled = []
            unchanged = []
            seen_ids = set()

            for device in devices:
                label = self._label(device)
                identifier = device.get("id")
                if isinstance(identifier, int) and not isinstance(identifier, bool):
                    protected_ids.add(str(identifier))
                try:
                    item = desired_host(device, self.openbao, self.config)
                    if str(item.netbox_id) in seen_ids:
                        raise DeviceValidationError(
                            "Duplicate NetBox device id",
                            code="duplicate_device_id",
                        )
                    seen_ids.add(str(item.netbox_id))
                    template = templates.get(item.template_name)
                    if template is None:
                        raise DeviceValidationError(
                            "Required Zabbix template is not installed",
                            code="template_not_found",
                            category="error",
                        )
                    existing = current_by_id.get(str(item.netbox_id))
                    payload = self._payload(item, groupid, template["templateid"])
                    if existing is None:
                        self._log_change("create", item.name)
                        client.host_create(payload)
                        created.append(item.name)
                    elif self._matches(
                        existing,
                        item,
                        groupid,
                        template["templateid"],
                    ):
                        unchanged.append(item.name)
                    else:
                        self._log_change("update", item.name)
                        payload["hostid"] = existing["hostid"]
                        existing_interfaces = [
                            interface
                            for interface in existing.get("interfaces") or []
                            if isinstance(interface, dict)
                            and str(interface.get("type")) == "2"
                            and interface.get("interfaceid")
                        ]
                        if len(existing_interfaces) == 1:
                            payload["interfaces"][0]["interfaceid"] = str(
                                existing_interfaces[0]["interfaceid"]
                            )
                        client.host_update(payload)
                        updated.append(item.name)
                except DeviceValidationError as exc:
                    issues.append(ZabbixSyncIssue(label, exc.code))
                    logger.warning(
                        "zabbix_device_skipped",
                        extra={"event": "zabbix_device_skipped", "device": label, "reason_code": exc.code},
                    )
                except DependencyError as exc:
                    raise ZabbixSyncFailure(exc.reason) from exc
                except ZabbixAPIError as exc:
                    if exc.code in {"connection_failed", "authentication_failed"}:
                        raise ZabbixSyncFailure(translate_zabbix_error(exc).reason) from exc
                    issues.append(ZabbixSyncIssue(label, "zabbix_apply_failed"))

            for netbox_id, host in sorted(current_by_id.items()):
                if netbox_id in protected_ids or str(host.get("status")) == "1":
                    continue
                try:
                    self._log_change(
                        "disable",
                        str(host.get("host") or host["hostid"]),
                    )
                    client.host_update({"hostid": host["hostid"], "status": 1})
                    disabled.append(str(host.get("host") or host["hostid"]))
                except ZabbixAPIError as exc:
                    if exc.code in {"connection_failed", "authentication_failed"}:
                        raise ZabbixSyncFailure(translate_zabbix_error(exc).reason) from exc
                    issues.append(
                        ZabbixSyncIssue(str(host.get("host") or "<unknown>"), "zabbix_disable_failed")
                    )

            for values in (created, updated, disabled, unchanged):
                values.sort()
            issues.sort(key=lambda issue: (issue.device or "", issue.code))
            summary = ZabbixSyncSummary(
                total=len(created) + len(updated) + len(disabled) + len(unchanged) + len(issues),
                created=len(created),
                updated=len(updated),
                disabled=len(disabled),
                unchanged=len(unchanged),
                errors=len(issues),
            )
            status = "partial_success" if issues else "success"
            logger.info(
                "zabbix_sync_completed",
                extra={
                    "event": "zabbix_sync_completed",
                    "sync_status": status,
                    "created_count": summary.created,
                    "updated_count": summary.updated,
                    "disabled_count": summary.disabled,
                    "unchanged_count": summary.unchanged,
                    "error_count": summary.errors,
                },
            )
            return ZabbixSyncResult(
                status,
                summary,
                tuple(created),
                tuple(updated),
                tuple(disabled),
                tuple(unchanged),
                tuple(issues),
            )
        finally:
            self.sync_lock.release()


__all__ = [
    "DesiredHost",
    "ZabbixSyncFailure",
    "ZabbixSyncIssue",
    "ZabbixSyncResult",
    "ZabbixSyncService",
    "desired_host",
]
