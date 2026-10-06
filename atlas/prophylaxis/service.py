"""NetBox/OpenBao orchestration for one normalized CPU collection."""

from dataclasses import dataclass
from ipaddress import ip_interface
import logging
from pathlib import Path

from atlas.device_credentials import DeviceValidationError
from atlas.network.base import ConnectionContext, SSHTimeouts
from atlas.network.errors import NetworkCollectionError
from atlas.network.registry import DriverRegistry
from atlas.network.transport import NetmikoTransport
from atlas.prophylaxis.errors import (
    CheckNotSelectedError,
    DeviceAmbiguousError,
    DeviceInactiveError,
    DeviceNotFoundError,
    DeviceResolutionError,
    MissingCredentialProfileError,
    MissingDeviceNameError,
    MissingManufacturerError,
    MissingPlatformError,
    MissingPrimaryIPError,
    ProphylaxisDisabledError,
)
from atlas.prophylaxis.models import (
    CPU_CHECK_NAME,
    CPUCheckResult,
    StoredCPUCheckResult,
)
from atlas.prophylaxis.store import ResultStoreError
from atlas.services.errors import DependencyError


logger = logging.getLogger("atlas.prophylaxis")


@dataclass(frozen=True)
class DeviceTarget:
    name: str
    host: str
    platform: str
    vendor: str
    credential_profile: str


@dataclass(frozen=True)
class PublicProphylaxisDevice:
    id: int
    name: str
    ip: str
    platform: str


def validate_cpu_selection(device: dict) -> None:
    status = device.get("status")
    status_value = status.get("value") if isinstance(status, dict) else status
    if status_value != "active":
        raise DeviceInactiveError()
    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    if fields.get("profylaxia_enabled") is not True:
        raise ProphylaxisDisabledError()
    checks = fields.get("profylaxia_checks")
    if not isinstance(checks, list) or CPU_CHECK_NAME not in checks:
        raise CheckNotSelectedError()


def device_target(device: dict) -> DeviceTarget:
    name = device.get("name") if isinstance(device, dict) else None
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
    platform = (
        platform_data.get("slug") if isinstance(platform_data, dict) else None
    )
    if not isinstance(platform, str) or not platform:
        raise MissingPlatformError()

    fields = device.get("custom_fields")
    fields = fields if isinstance(fields, dict) else {}
    credential_profile = fields.get("credential_profile")
    if not isinstance(credential_profile, str) or not credential_profile:
        raise MissingCredentialProfileError()

    device_type = device.get("device_type")
    manufacturer = (
        device_type.get("manufacturer")
        if isinstance(device_type, dict)
        else None
    )
    vendor = manufacturer.get("slug") if isinstance(manufacturer, dict) else None
    if not isinstance(vendor, str) or not vendor:
        raise MissingManufacturerError()

    return DeviceTarget(
        name=name.strip(),
        host=host,
        platform=platform,
        vendor=vendor,
        credential_profile=credential_profile,
    )


class ProphylaxisService:
    def __init__(
        self,
        netbox,
        openbao,
        *,
        known_hosts_file: Path,
        strict_host_keys: bool,
        timeouts: SSHTimeouts,
        registry: DriverRegistry | None = None,
        transport=None,
        result_store=None,
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.known_hosts_file = Path(known_hosts_file)
        self.strict_host_keys = strict_host_keys
        self.timeouts = timeouts
        self.registry = registry or DriverRegistry()
        self.transport = transport or NetmikoTransport()
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

    def collect_cpu(self, identifier: int | str) -> CPUCheckResult:
        device_name = str(identifier)
        platform = None
        try:
            device = self._get_device(identifier)
            raw_name = device.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                device_name = raw_name.strip()
            validate_cpu_selection(device)
            target = device_target(device)
            device_name = target.name
            platform = target.platform
            driver_class = self.registry.driver_class(target.platform)
            credentials = self.openbao.get_device_credentials(
                target.vendor,
                target.credential_profile,
            )
            context = ConnectionContext(
                device_name=target.name,
                host=target.host,
                platform=target.platform,
                username=credentials["username"],
                password=credentials["password"],
                enable_password=credentials.get("enable_password"),
                strict_host_keys=self.strict_host_keys,
                known_hosts_file=self.known_hosts_file,
                timeouts=self.timeouts,
            )
            driver = driver_class(context, self.transport)
            values = driver.get_cpu_utilization()
        except (DeviceResolutionError, NetworkCollectionError) as exc:
            return self._error(device_name, platform, exc.code)
        except DeviceValidationError as exc:
            return self._error(device_name, platform, exc.code)
        except DependencyError:
            return self._error(device_name, platform, "dependency_unavailable")

        logger.info(
            "cpu_collection_completed",
            extra={
                "event": "cpu_collection_completed",
                "device": device_name,
                "platform": platform,
                "check": CPU_CHECK_NAME,
                "status": "ok",
            },
        )
        return CPUCheckResult(
            device=device_name,
            platform=platform,
            status="ok",
            values=values,
        )

    def list_devices(self) -> list[PublicProphylaxisDevice]:
        devices = self.netbox.get_prophylaxis_devices()
        result = []
        for device in devices:
            try:
                validate_cpu_selection(device)
                target = device_target(device)
                self.registry.driver_class(target.platform)
                identifier = device.get("id")
                if isinstance(identifier, bool) or not isinstance(identifier, int):
                    continue
            except (DeviceResolutionError, NetworkCollectionError):
                continue
            result.append(
                PublicProphylaxisDevice(
                    id=identifier,
                    name=target.name,
                    ip=target.host,
                    platform=target.platform,
                )
            )
        return sorted(result, key=lambda item: item.name.casefold())

    def run_cpu_check(self, identifier: int | str) -> StoredCPUCheckResult:
        if self.result_store is None:
            raise ResultStoreError()
        return self.result_store.save(self.collect_cpu(identifier))

    def result_history(self, limit: int = 20) -> list[StoredCPUCheckResult]:
        if self.result_store is None:
            raise ResultStoreError()
        return self.result_store.list(limit)

    @staticmethod
    def _error(device: str, platform: str | None, code: str) -> CPUCheckResult:
        logger.warning(
            "cpu_collection_failed",
            extra={
                "event": "cpu_collection_failed",
                "device": device,
                "platform": platform,
                "check": CPU_CHECK_NAME,
                "reason_code": code,
            },
        )
        return CPUCheckResult(
            device=device,
            platform=platform,
            status="error",
            error=code,
        )


__all__ = [
    "DeviceTarget",
    "ProphylaxisService",
    "PublicProphylaxisDevice",
    "device_target",
    "validate_cpu_selection",
]
