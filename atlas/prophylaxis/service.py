"""NetBox/OpenBao orchestration for normalized Ansible-backed checks."""

from dataclasses import dataclass
from ipaddress import ip_interface
import logging

from atlas.device_credentials import DeviceValidationError
from atlas.prophylaxis.collectors import CollectorRegistry
from atlas.prophylaxis.errors import (
    AmbiguousServicePortError,
    CheckNotSelectedError,
    DeviceAmbiguousError,
    DeviceInactiveError,
    DeviceNotFoundError,
    DeviceResolutionError,
    InvalidServicePortError,
    MissingCredentialProfileError,
    MissingDeviceNameError,
    MissingManufacturerError,
    MissingPlatformError,
    MissingPrimaryIPError,
    ProphylaxisDisabledError,
    ProphylaxisExecutionError,
)
from atlas.prophylaxis.models import (
    CheckId,
    CheckResult,
    StoredCheckResult,
    expand_check_selection,
)
from atlas.prophylaxis.platforms import (
    FORTIOS_SSH_PROFILE,
    AnsiblePlatformProfile,
    AnsiblePlatformRegistry,
)
from atlas.prophylaxis.store import ResultStoreError
from atlas.services.errors import DependencyError
from atlas.services.device_resolution import (
    DeviceResolutionError as SharedResolutionError,
    resolve_tcp_service_port,
)


logger = logging.getLogger("atlas.prophylaxis")


@dataclass(frozen=True)
class DeviceTarget:
    id: int
    name: str
    host: str
    platform: str
    vendor: str
    credential_profile: str
    model: str | None = None


@dataclass(frozen=True)
class PublicProphylaxisDevice:
    id: int
    name: str
    ip: str
    platform: str


def selected_check_values(raw_checks: object) -> set[str]:
    """Return exact stored values from NetBox multiselect serialization."""
    if not isinstance(raw_checks, list):
        return set()
    values = set()
    for item in raw_checks:
        if isinstance(item, str):
            values.add(item)
        elif isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str):
                values.add(value)
    return values


def validate_check_selection(device: dict, check_id: CheckId) -> None:
    status = device.get("status")
    status_value = status.get("value") if isinstance(status, dict) else status
    if status_value != "active":
        raise DeviceInactiveError()
    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    if fields.get("profylaxia_enabled") is not True:
        raise ProphylaxisDisabledError()
    checks, _unknown = expand_check_selection(
        selected_check_values(fields.get("profylaxia_checks"))
    )
    if check_id not in checks:
        raise CheckNotSelectedError()


def validate_cpu_selection(device: dict) -> None:
    validate_check_selection(device, CheckId.CPU_UTILIZATION)


def device_target(device: dict) -> DeviceTarget:
    identifier = device.get("id") if isinstance(device, dict) else None
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise DeviceNotFoundError()

    name = device.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MissingDeviceNameError()

    primary = (
        device.get("primary_ip4")
        or device.get("primary_ip6")
        or device.get("primary_ip")
        or {}
    )
    address = primary.get("address") if isinstance(primary, dict) else None
    try:
        host = str(ip_interface(address).ip)
    except (TypeError, ValueError):
        raise MissingPrimaryIPError() from None

    platform_data = device.get("platform")
    platform = platform_data.get("slug") if isinstance(platform_data, dict) else None
    if not isinstance(platform, str) or not platform:
        raise MissingPlatformError()

    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    credential_profile = fields.get("credential_profile")
    if not isinstance(credential_profile, str) or not credential_profile:
        raise MissingCredentialProfileError()

    device_type = device.get("device_type")
    manufacturer = device_type.get("manufacturer") if isinstance(device_type, dict) else None
    vendor = manufacturer.get("slug") if isinstance(manufacturer, dict) else None
    if not isinstance(vendor, str) or not vendor:
        raise MissingManufacturerError()
    model = device_type.get("model") if isinstance(device_type, dict) else None
    if not isinstance(model, str) or not model.strip():
        model = None

    return DeviceTarget(
        id=identifier,
        name=name.strip(),
        host=host,
        platform=platform,
        vendor=vendor,
        credential_profile=credential_profile,
        model=model.strip() if model else None,
    )


def resolve_service_port(services: object, default_port: int) -> int:
    """Compatibility wrapper around the shared NetBox service resolver."""
    try:
        return resolve_tcp_service_port(services, default_port)
    except SharedResolutionError as exc:
        if exc.code == "ambiguous_service_port":
            raise AmbiguousServicePortError() from None
        raise InvalidServicePortError() from None


def resolve_execution_profile(
    netbox,
    target: DeviceTarget,
    profile: AnsiblePlatformProfile,
) -> tuple[AnsiblePlatformProfile, int]:
    """Resolve the desired transport and port before reading credentials."""
    if profile.slug == "fortios":
        services = netbox.get_device_services(target.id, "fortios-api")
        if not services:
            return FORTIOS_SSH_PROFILE, FORTIOS_SSH_PROFILE.default_port
        return profile, resolve_service_port(services, profile.default_port)
    if profile.netbox_service_name is None:
        return profile, profile.default_port
    services = netbox.get_device_services(target.id, profile.netbox_service_name)
    return profile, resolve_service_port(services, profile.default_port)


class ProphylaxisService:
    def __init__(
        self,
        netbox,
        openbao,
        *,
        backend,
        registry: AnsiblePlatformRegistry | None = None,
        collector_registry: CollectorRegistry | None = None,
        result_store=None,
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.backend = backend
        self.registry = registry or AnsiblePlatformRegistry()
        self.collector_registry = collector_registry or CollectorRegistry()
        self.result_store = result_store

    def _get_device(self, identifier: int | str) -> dict:
        if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
            raise DeviceNotFoundError()
        if isinstance(identifier, int):
            params = {"id": identifier}
        else:
            name = identifier.strip()
            if not name:
                raise DeviceNotFoundError()
            params = {"name": name}

        devices = self.netbox.get_all("/api/dcim/devices/", params=params)
        if not devices:
            raise DeviceNotFoundError()
        if len(devices) != 1:
            raise DeviceAmbiguousError()
        return devices[0]

    def _telnet_fallback_port(
        self,
        target: DeviceTarget,
        profile: AnsiblePlatformProfile,
    ) -> int | None:
        service_name = profile.telnet_fallback_service_name
        if service_name is None:
            return None
        services = self.netbox.get_device_services(target.id, service_name)
        if not services:
            return None
        return resolve_service_port(
            services,
            profile.telnet_fallback_default_port,
        )

    def collect(self, identifier: int | str, check_id: CheckId | str) -> CheckResult:
        try:
            check_id = CheckId(check_id)
        except (TypeError, ValueError):
            raise ValueError("check_id must be a known CheckId") from None

        device_id = identifier if isinstance(identifier, int) and not isinstance(identifier, bool) else None
        device_name = str(identifier)
        platform = None
        try:
            device = self._get_device(identifier)
            raw_name = device.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                device_name = raw_name.strip()
            raw_id = device.get("id")
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                device_id = raw_id
            validate_check_selection(device, check_id)
            target = device_target(device)
            device_id = target.id
            device_name = target.name
            platform = target.platform
            base_profile = self.registry.resolve(target.platform)
            profile, connection_port = resolve_execution_profile(
                self.netbox,
                target,
                base_profile,
            )
            try:
                collector = self.collector_registry.resolve_check(
                    check_id,
                    profile,
                )
            except ValueError:
                return self._error(
                    target.name,
                    target.id,
                    target.platform,
                    "unsupported_check",
                    check_id=check_id,
                    status="unsupported",
                )
            if not profile.supports_collector(collector.id):
                return self._error(
                    target.name,
                    target.id,
                    target.platform,
                    "unsupported_check",
                    check_id=check_id,
                    status="unsupported",
                )
            execution_profile = profile
            telnet_fallback_port = self._telnet_fallback_port(
                target,
                execution_profile,
            )
            credentials = self.openbao.get_device_credentials(
                target.vendor,
                target.credential_profile,
                authentication=execution_profile.authentication,
                purpose="prophylaxis",
            )
            raw_result = self.backend.execute(
                host=target.host,
                port=connection_port,
                profile=execution_profile,
                collector_id=collector.id,
                check_id=check_id,
                credentials=credentials,
                telnet_fallback_port=telnet_fallback_port,
            )
            if raw_result.status != "ok":
                return self._error(
                    target.name,
                    target.id,
                    target.platform,
                    raw_result.error_code or "ansible_execution_failed",
                    check_id=check_id,
                    status=raw_result.status,
                )
            values = collector.normalize(check_id, raw_result)
        except (DeviceResolutionError, ProphylaxisExecutionError) as exc:
            return self._error(device_name, device_id, platform, exc.code, check_id=check_id)
        except DeviceValidationError as exc:
            return self._error(device_name, device_id, platform, exc.code, check_id=check_id)
        except DependencyError:
            return self._error(
                device_name,
                device_id,
                platform,
                "dependency_unavailable",
                check_id=check_id,
            )

        logger.info(
            "prophylaxis_collection_completed",
            extra={
                "event": "prophylaxis_collection_completed",
                "device": device_name,
                "platform": platform,
                "port": connection_port,
                "check": check_id.value,
                "status": "ok",
            },
        )
        return CheckResult(
            device_id=device_id,
            device=device_name,
            platform=platform,
            check=check_id,
            status="ok",
            values=values,
        )

    def collect_cpu(self, identifier: int | str) -> CheckResult:
        return self.collect(identifier, CheckId.CPU_UTILIZATION)

    def list_devices(self) -> list[PublicProphylaxisDevice]:
        devices = self.netbox.get_prophylaxis_devices()
        result = []
        for device in devices:
            label = device.get("name") or device.get("id") or "unknown"
            try:
                validate_cpu_selection(device)
                target = device_target(device)
                profile = self.registry.resolve(target.platform)
                collector = self.collector_registry.resolve_check(
                    CheckId.CPU_UTILIZATION,
                    profile,
                )
                if not profile.supports_collector(collector.id):
                    continue
            except (DeviceResolutionError, ProphylaxisExecutionError) as exc:
                logger.warning(
                    "prophylaxis_device_skipped",
                    extra={
                        "event": "prophylaxis_device_skipped",
                        "device": label,
                        "reason_code": exc.code,
                    },
                )
                continue
            result.append(
                PublicProphylaxisDevice(
                    id=target.id,
                    name=target.name,
                    ip=target.host,
                    platform=target.platform,
                )
            )
        return sorted(result, key=lambda item: item.name.casefold())

    def run_check(
        self,
        identifier: int | str,
        check_id: CheckId | str,
    ) -> StoredCheckResult:
        if self.result_store is None:
            raise ResultStoreError()
        return self.result_store.save(self.collect(identifier, check_id))

    def run_cpu_check(self, identifier: int | str) -> StoredCheckResult:
        return self.run_check(identifier, CheckId.CPU_UTILIZATION)

    def result_history(self, limit: int = 20) -> list[StoredCheckResult]:
        if self.result_store is None:
            raise ResultStoreError()
        return self.result_store.list(limit)

    @staticmethod
    def _error(
        device: str,
        device_id: int | None,
        platform: str | None,
        code: str,
        *,
        check_id: CheckId = CheckId.CPU_UTILIZATION,
        status: str = "error",
    ) -> CheckResult:
        logger.warning(
            "prophylaxis_collection_failed",
            extra={
                "event": "prophylaxis_collection_failed",
                "device": device,
                "platform": platform,
                "check": check_id.value,
                "status": status,
                "reason_code": code,
            },
        )
        return CheckResult(
            device_id=device_id,
            device=device,
            platform=platform,
            check=check_id,
            status=status,
            error_code=code,
        )


__all__ = [
    "DeviceTarget",
    "ProphylaxisService",
    "PublicProphylaxisDevice",
    "device_target",
    "resolve_execution_profile",
    "resolve_service_port",
    "selected_check_values",
    "validate_check_selection",
    "validate_cpu_selection",
]
