"""FortiOS SSH raw collection catalog and validation helpers."""

from __future__ import annotations

import re


FORTIOS_RAW_PLATFORMS = frozenset({"fortios"})

FORTIOS_IDENTITY_COMMANDS = (
    "get system status",
    "get system performance status",
    "get system interface",
)

FORTIOS_FULL_COMMANDS = (
    "get system status",
    "get system performance status",
    "get system interface",
    "get system interface physical",
    "diagnose hardware sysinfo memory",
    "diagnose hardware deviceinfo disk",
    "diagnose hardware deviceinfo tpm",
    "diagnose hardware deviceinfo psu",
    "get system ha status",
    "get router info routing-table all",
    "get router info bgp network",
    "get system fortiguard-service status",
    "get vpn certificate local details",
    "get vpn certificate ca details",
    "diagnose vpn tunnel list",
    "execute log filter category event",
    "execute log filter view-lines 50",
    "execute log display",
    "diagnose sys ha checksum cluster",
    "diagnose sys ha history read",
    "get router info bgp summary",
    "get router info ospf neighbor",
    "diagnose sys ntp status",
    "diagnose sys session stat",
    "diagnose debug crashlog read",
    "diagnose hardware sysinfo conserve",
    "get system arp",
    "get sys interface transceiver",
    "diagnose sys sdwan health-check",
    "get log fortianalyzer setting",
)

FORTIOS_MAX_DISCOVERED_INTERFACES = 64
FORTIOS_MAX_COMMANDS = len(FORTIOS_FULL_COMMANDS) + FORTIOS_MAX_DISCOVERED_INTERFACES

FORTIOS_INTERFACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
FORTIOS_NIC_COMMAND = re.compile(
    r"diagnose hardware deviceinfo nic ([A-Za-z0-9][A-Za-z0-9_.-]{0,63})"
)


def is_allowed_fortios_result_command(command: object) -> bool:
    """Accept only catalog commands or a strictly validated discovered NIC command."""
    if not isinstance(command, str):
        return False
    return command in FORTIOS_FULL_COMMANDS or FORTIOS_NIC_COMMAND.fullmatch(command) is not None


__all__ = [
    "FORTIOS_FULL_COMMANDS",
    "FORTIOS_IDENTITY_COMMANDS",
    "FORTIOS_INTERFACE_NAME",
    "FORTIOS_MAX_COMMANDS",
    "FORTIOS_MAX_DISCOVERED_INTERFACES",
    "FORTIOS_NIC_COMMAND",
    "FORTIOS_RAW_PLATFORMS",
    "is_allowed_fortios_result_command",
]
