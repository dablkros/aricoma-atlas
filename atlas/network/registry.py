"""Explicit NetBox-platform to Atlas-driver registry."""

from typing import Type

from atlas.network.base import CommandTransport, ConnectionContext, NetworkDriver
from atlas.network.drivers.cisco_ios import CiscoIOSDriver
from atlas.network.drivers.cisco_smb import CiscoSMBDriver
from atlas.network.drivers.fortios import FortiOSDriver
from atlas.network.errors import UnsupportedPlatformError


DEFAULT_DRIVER_MAPPING: dict[str, Type[NetworkDriver]] = {
    "cisco-ios": CiscoIOSDriver,
    "cisco-ios-xe": CiscoIOSDriver,
    "cisco-cbs": CiscoSMBDriver,
    "fortios": FortiOSDriver,
}


class DriverRegistry:
    def __init__(
        self,
        mapping: dict[str, Type[NetworkDriver]] | None = None,
    ) -> None:
        self._mapping = dict(mapping or DEFAULT_DRIVER_MAPPING)

    def driver_class(self, platform: str) -> Type[NetworkDriver]:
        try:
            return self._mapping[platform]
        except (KeyError, TypeError):
            raise UnsupportedPlatformError() from None

    def create(
        self,
        context: ConnectionContext,
        transport: CommandTransport,
    ) -> NetworkDriver:
        return self.driver_class(context.platform)(context, transport)


__all__ = ["DEFAULT_DRIVER_MAPPING", "DriverRegistry"]
