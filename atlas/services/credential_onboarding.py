"""Plan and apply device credential onboarding without exposing secret values."""

from copy import deepcopy

from atlas.api.schemas.credentials import CredentialOnboardingRequest
from atlas.device_credentials import (
    DeviceValidationError,
    credential_path,
    validate_admin_secret,
    validate_snmp_secret,
)
from atlas.platforms import load_platform_config
from atlas.prophylaxis.platforms import ANSIBLE_PLATFORMS
from atlas.services.errors import DependencyError


class CredentialOnboardingInvalid(ValueError):
    pass


class CredentialOnboardingService:
    def __init__(self, netbox, secret_reader, secret_writer) -> None:
        self.netbox = netbox
        self.secret_reader = secret_reader
        self.secret_writer = secret_writer
        self.platform_config = load_platform_config()

    @staticmethod
    def _nested_value(value, key="value"):
        return value.get(key) if isinstance(value, dict) else value

    def options(self) -> dict:
        devices = self.netbox.get_all(
            "/api/dcim/devices/", params={"status": "active"}
        )
        profiles = sorted({
            str((device.get("custom_fields") or {}).get("credential_profile")).strip()
            for device in devices
            if (device.get("custom_fields") or {}).get("credential_profile")
        })
        result_devices = []
        for device in devices:
            device_type = device.get("device_type") or {}
            manufacturer = device_type.get("manufacturer") or {}
            platform = device.get("platform") or {}
            tenant = device.get("tenant") or {}
            fields = device.get("custom_fields") or {}
            result_devices.append({
                "id": device.get("id"),
                "name": device.get("name"),
                "tenant": {
                    "id": tenant.get("id"),
                    "name": tenant.get("name"),
                    "slug": tenant.get("slug"),
                },
                "manufacturer": {
                    "name": manufacturer.get("name"),
                    "slug": manufacturer.get("slug"),
                },
                "platform": platform.get("slug"),
                "credential_profile": (
                    fields.get("credential_profile")
                ),
                "prophylaxis_enabled": fields.get("profylaxia_enabled") is True,
                "oxidized_enabled": fields.get("oxidized_enabled") is True,
                "monitoring_enabled": fields.get("monitoring_enabled") is True,
            })
        tenants = sorted(
            {
                (
                    device["tenant"]["id"],
                    device["tenant"]["name"],
                    device["tenant"]["slug"],
                )
                for device in result_devices
                if device["tenant"]["id"] is not None
                and device["tenant"]["name"]
                and device["tenant"]["slug"]
            },
            key=lambda item: str(item[1]).casefold(),
        )
        platforms = []
        oxidized = self.platform_config.get("oxidized_platform_mapping", {})
        for slug, definition in sorted(self.platform_config["platforms"].items()):
            if slug not in ANSIBLE_PLATFORMS and slug not in oxidized:
                continue
            auth = []
            if slug in ANSIBLE_PLATFORMS:
                auth.append(ANSIBLE_PLATFORMS[slug].authentication)
                if slug == "fortios":
                    auth.append("ssh")
            platforms.append({
                "slug": slug,
                "name": definition["name"],
                "manufacturer": definition["manufacturer"],
                "prophylaxis_auth": sorted(set(auth)),
                "oxidized_supported": slug in oxidized,
            })
        return {
            "devices": result_devices,
            "tenants": [
                {"id": tenant_id, "name": name, "slug": slug}
                for tenant_id, name, slug in tenants
            ],
            "platforms": platforms,
            "profiles": profiles,
        }

    def _devices(self, body: CredentialOnboardingRequest) -> tuple[list[dict], str, dict]:
        devices = [
            self.netbox.get(f"/api/dcim/devices/{device_id}/")
            for device_id in body.device_ids
        ]
        if any(
            self._nested_value(device.get("status")) != "active"
            for device in devices
        ):
            raise CredentialOnboardingInvalid("Only active devices can be onboarded")
        tenants = [device.get("tenant") or {} for device in devices]
        if any(tenant.get("id") is None for tenant in tenants):
            raise CredentialOnboardingInvalid(
                "All selected devices must belong to a tenant"
            )
        tenant_pairs = {
            (tenant.get("id"), tenant.get("slug"), tenant.get("name"))
            for tenant in tenants
        }
        if len(tenant_pairs) != 1:
            raise CredentialOnboardingInvalid(
                "All selected devices must belong to the same tenant"
            )
        tenant_id, tenant_slug, tenant_name = tenant_pairs.pop()
        manufacturers = [
            ((device.get("device_type") or {}).get("manufacturer") or {})
            for device in devices
        ]
        vendor_pairs = {
            (manufacturer.get("slug"), manufacturer.get("name"))
            for manufacturer in manufacturers
        }
        if len(vendor_pairs) != 1:
            raise CredentialOnboardingInvalid(
                "All selected devices must have the same manufacturer"
            )
        vendor, vendor_name = vendor_pairs.pop()
        definition = self.platform_config["platforms"].get(body.platform)
        if not vendor or not definition:
            raise CredentialOnboardingInvalid("Device manufacturer or platform is missing")
        if str(vendor_name).casefold() != str(definition["manufacturer"]).casefold():
            raise CredentialOnboardingInvalid("Platform does not belong to the device manufacturer")
        return devices, vendor, {
            "id": tenant_id,
            "slug": tenant_slug,
            "name": tenant_name,
        }

    def _requested_secrets(self, body: CredentialOnboardingRequest) -> list[tuple[str, dict]]:
        try:
            values = []
            if body.prophylaxis_enabled:
                if body.platform not in ANSIBLE_PLATFORMS:
                    raise CredentialOnboardingInvalid("Platform is not supported by Prophylaxia")
                allowed_auth = {ANSIBLE_PLATFORMS[body.platform].authentication}
                if body.platform == "fortios":
                    allowed_auth.add("ssh")
                if body.prophylaxis_auth not in allowed_auth:
                    raise CredentialOnboardingInvalid("Authentication is not supported for this platform")
                raw = (
                    body.prophylaxis_api.model_dump()
                    if body.prophylaxis_auth == "api_token"
                    else body.prophylaxis_ssh.model_dump(exclude_none=True)
                )
                values.append((
                    "prophylaxis",
                    validate_admin_secret(raw, authentication=body.prophylaxis_auth),
                ))
            if body.oxidized_enabled:
                if body.platform not in self.platform_config.get("oxidized_platform_mapping", {}):
                    raise CredentialOnboardingInvalid("Platform is not supported by Oxidized")
                values.append((
                    "oxidized",
                    validate_admin_secret(body.oxidized.model_dump(exclude_none=True)),
                ))
            if body.monitoring_enabled:
                values.append((
                    "snmp",
                    validate_snmp_secret({"version": "2c", **body.snmp.model_dump()}),
                ))
            return values
        except DeviceValidationError as exc:
            raise CredentialOnboardingInvalid(str(exc)) from None

    def _secret_operation(self, vendor: str, profile: str, purpose: str, value: dict, replace: bool):
        path = credential_path(vendor, profile, purpose=purpose)
        try:
            current, metadata = self.secret_reader.read_secret_with_metadata(path)
        except DependencyError as exc:
            if exc.reason != "secret_not_found":
                raise
            return {"target": "openbao", "purpose": purpose, "path": path,
                    "action": "CREATE", "version": 0, "blocked": False}
        if current == value:
            return {"target": "openbao", "purpose": purpose, "path": path,
                    "action": "UNCHANGED", "version": metadata.get("version"), "blocked": False}
        version = metadata.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DependencyError("invalid_response")
        if not replace:
            return {"target": "openbao", "purpose": purpose, "path": path,
                    "action": "CONFLICT", "version": version, "blocked": True,
                    "reason": "credential_exists_and_differs"}
        return {"target": "openbao", "purpose": purpose, "path": path,
                "action": "UPDATE", "version": version, "blocked": False}

    def plan(self, body: CredentialOnboardingRequest) -> dict:
        devices, vendor, tenant = self._devices(body)
        secrets = self._requested_secrets(body)
        operations = [
            self._secret_operation(
                vendor, body.credential_profile, purpose, value, body.replace_existing
            )
            for purpose, value in secrets
        ]
        platforms = self.netbox.get_all(
            "/api/dcim/platforms/", params={"slug": body.platform}
        )
        if len(platforms) != 1:
            raise CredentialOnboardingInvalid("Selected platform is not provisioned uniquely in NetBox")
        for device in devices:
            device_ref = {"id": device.get("id"), "name": device.get("name")}
            services = self.netbox.get_device_services(device["id"], "fortios-api")
            if body.platform == "fortios" and body.prophylaxis_enabled:
                if body.prophylaxis_auth == "api_token":
                    port = body.fortios_api_port or 443
                    matching = [service for service in services if (
                        self._nested_value(service.get("protocol")) == "tcp"
                        and service.get("ports") == [port]
                    )]
                    if not services:
                        operations.append({"target": "netbox", "object": "fortios-api",
                                           "device": device_ref, "action": "CREATE",
                                           "port": port, "blocked": False})
                    elif len(services) == 1 and len(matching) == 1:
                        operations.append({"target": "netbox", "object": "fortios-api",
                                           "device": device_ref, "action": "UNCHANGED",
                                           "port": port, "blocked": False})
                    else:
                        operations.append({"target": "netbox", "object": "fortios-api",
                                           "device": device_ref, "action": "CONFLICT",
                                           "blocked": True,
                                           "reason": "fortios_api_service_conflict"})
                elif services:
                    operations.append({"target": "netbox", "object": "fortios-api",
                                       "device": device_ref, "action": "CONFLICT",
                                       "blocked": True,
                                       "reason": "fortios_api_service_selects_api_transport"})
            current_platform = device.get("platform") or {}
            current_slug = current_platform.get("slug")
            if current_slug and current_slug != body.platform:
                operations.append({"target": "netbox", "object": "device",
                                   "device": device_ref, "action": "CONFLICT",
                                   "blocked": True, "reason": "platform_mismatch"})
                continue
            fields = deepcopy(device.get("custom_fields") or {})
            desired_fields = {
                **fields,
                "credential_profile": body.credential_profile,
                "profylaxia_enabled": body.prophylaxis_enabled,
                "oxidized_enabled": body.oxidized_enabled,
                "monitoring_enabled": body.monitoring_enabled,
            }
            changed = current_platform.get("id") != platforms[0]["id"] or fields != desired_fields
            operations.append({"target": "netbox", "object": "device",
                               "device": device_ref,
                               "action": "UPDATE" if changed else "UNCHANGED",
                               "blocked": False})
        return {
            "devices": [
                {"id": device.get("id"), "name": device.get("name")}
                for device in devices
            ],
            "manufacturer": vendor,
            "tenant": tenant,
            "platform": body.platform,
            "credential_profile": body.credential_profile,
            "apply_allowed": not any(item.get("blocked") for item in operations),
            "operations": operations,
        }

    def apply(self, body: CredentialOnboardingRequest, *, expected_plan: dict) -> dict:
        preview = expected_plan
        if not preview["apply_allowed"]:
            raise CredentialOnboardingInvalid("Resolve credential conflicts before apply")
        devices, vendor, _tenant = self._devices(body)
        secret_values = dict(self._requested_secrets(body))
        secret_operations = {
            operation.get("purpose"): operation
            for operation in preview["operations"]
            if operation.get("target") == "openbao"
        }
        for purpose, value in secret_values.items():
            operation = secret_operations.get(purpose)
            if not operation:
                raise CredentialOnboardingInvalid("Confirmed credential plan is incomplete")
            current = self._secret_operation(
                vendor,
                body.credential_profile,
                purpose,
                value,
                body.replace_existing,
            )
            if current != operation:
                raise CredentialOnboardingInvalid(
                    "Credential state changed; create a new plan"
                )
            if operation["action"] in {"CREATE", "UPDATE"}:
                self.secret_writer.write_secret(
                    operation["path"], value, cas=operation["version"]
                )
        platform = self.netbox.get_all(
            "/api/dcim/platforms/", params={"slug": body.platform}
        )[0]
        for device in devices:
            if body.platform == "fortios" and body.prophylaxis_enabled:
                services = self.netbox.get_device_services(device["id"], "fortios-api")
                service_operation = next(
                    (
                        operation
                        for operation in preview["operations"]
                        if operation.get("object") == "fortios-api"
                        and (operation.get("device") or {}).get("id") == device["id"]
                    ),
                    None,
                )
                if body.prophylaxis_auth == "api_token":
                    if service_operation is None:
                        raise CredentialOnboardingInvalid("Confirmed service plan is incomplete")
                    if service_operation["action"] == "CREATE" and services:
                        raise CredentialOnboardingInvalid(
                            "FortiOS API service state changed; create a new plan"
                        )
                    if service_operation["action"] == "CREATE":
                        self.netbox.post("/api/ipam/services/", {
                            "name": "fortios-api",
                            "device": device["id"],
                            "protocol": "tcp",
                            "ports": [body.fortios_api_port or 443],
                            "description": "Aricoma Atlas FortiOS REST API",
                        })
                    elif not (
                        len(services) == 1
                        and self._nested_value(services[0].get("protocol")) == "tcp"
                        and services[0].get("ports") == [body.fortios_api_port or 443]
                    ):
                        raise CredentialOnboardingInvalid(
                            "FortiOS API service state changed; create a new plan"
                        )
                elif services:
                    raise CredentialOnboardingInvalid(
                        "FortiOS API service state changed; create a new plan"
                    )
            fields = deepcopy(device.get("custom_fields") or {})
            fields.update({
                "credential_profile": body.credential_profile,
                "profylaxia_enabled": body.prophylaxis_enabled,
                "oxidized_enabled": body.oxidized_enabled,
                "monitoring_enabled": body.monitoring_enabled,
            })
            self.netbox.patch(f"/api/dcim/devices/{device['id']}/", {
                "platform": platform["id"], "custom_fields": fields,
            })
        result = self.plan(body)
        result["status"] = "APPLIED"
        return result


__all__ = ["CredentialOnboardingInvalid", "CredentialOnboardingService"]
