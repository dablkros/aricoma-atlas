"""Safe read and node-operation boundary for the Oxidized Operations UI."""

import re
from dataclasses import dataclass
from typing import List

from atlas.oxidized_inventory import InventoryError
from atlas.services.errors import DependencyError


DEVICE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class OxidizedOperationFailure(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class PublicOxidizedDevice:
    name: str
    ip: str
    model: str


@dataclass(frozen=True)
class OxidizedOverview:
    status: str
    netbox_enabled_devices: int
    runtime_inventory_devices: int
    inventory_issues: int


class OxidizedOperationsService:
    def __init__(self, netbox, oxidized, inventory_store) -> None:
        self.netbox = netbox
        self.oxidized = oxidized
        self.inventory_store = inventory_store

    def _runtime_inventory(self) -> List[dict]:
        try:
            return self.inventory_store.read()
        except InventoryError as exc:
            raise OxidizedOperationFailure("inventory_invalid", 500) from exc

    def list_devices(self) -> List[PublicOxidizedDevice]:
        return [
            PublicOxidizedDevice(
                name=entry["name"],
                ip=entry["ip"],
                model=entry["model"],
            )
            for entry in self._runtime_inventory()
        ]

    def status(self) -> OxidizedOverview:
        try:
            enabled = self.netbox.get_oxidized_devices()
        except DependencyError as exc:
            code = (
                "netbox_authentication_failed"
                if exc.reason == "authentication_failed"
                else "netbox_unavailable"
            )
            raise OxidizedOperationFailure(code, 503) from exc

        runtime = self._runtime_inventory()
        enabled_names = {
            item.get("name")
            for item in enabled
            if isinstance(item.get("name"), str) and item["name"]
        }
        runtime_names = {item["name"] for item in runtime}
        invalid_or_duplicate_enabled = len(enabled) - len(enabled_names)
        issue_count = (
            len(enabled_names.symmetric_difference(runtime_names))
            + invalid_or_duplicate_enabled
        )
        return OxidizedOverview(
            status="healthy" if issue_count == 0 else "degraded",
            netbox_enabled_devices=len(enabled),
            runtime_inventory_devices=len(runtime),
            inventory_issues=issue_count,
        )

    def queue_backup(self, device: str) -> PublicOxidizedDevice:
        if not isinstance(device, str) or not DEVICE_NAME_PATTERN.fullmatch(device):
            raise OxidizedOperationFailure("invalid_device_name", 422)

        devices = {item.name: item for item in self.list_devices()}
        selected = devices.get(device)
        if selected is None:
            raise OxidizedOperationFailure("device_not_found", 404)

        try:
            self.oxidized.queue_backup(device)
        except DependencyError as exc:
            code = (
                "oxidized_authentication_failed"
                if exc.reason == "authentication_failed"
                else "oxidized_unavailable"
            )
            raise OxidizedOperationFailure(code, 503) from exc
        return selected


__all__ = [
    "DEVICE_NAME_PATTERN",
    "OxidizedOperationFailure",
    "OxidizedOperationsService",
    "OxidizedOverview",
    "PublicOxidizedDevice",
]
