"""Cisco IOS and IOS XE CPU collection."""

import re

from atlas.network.base import NetworkDriver
from atlas.network.errors import ParseFailedError
from atlas.prophylaxis.models import CPUUtilizationValues


CPU_PATTERN = re.compile(
    r"CPU\s+utilization\s+for\s+five\s+seconds:\s*"
    r"(?P<current>\d+(?:\.\d+)?)%\s*/\s*"
    r"(?P<interrupt>\d+(?:\.\d+)?)%\s*;\s*"
    r"one\s+minute:\s*(?P<one_minute>\d+(?:\.\d+)?)%\s*;\s*"
    r"five\s+minutes:\s*(?P<five_minutes>\d+(?:\.\d+)?)%",
    re.IGNORECASE,
)


def parse_cpu_output(raw_output: str) -> CPUUtilizationValues:
    match = CPU_PATTERN.search(raw_output) if isinstance(raw_output, str) else None
    if match is None:
        raise ParseFailedError()
    values = {name: float(value) for name, value in match.groupdict().items()}
    if any(value < 0 or value > 100 for value in values.values()):
        raise ParseFailedError()
    return CPUUtilizationValues(
        current_percent=values["current"],
        one_minute_percent=values["one_minute"],
        five_minute_percent=values["five_minutes"],
    )


class CiscoIOSDriver(NetworkDriver):
    platform_device_type = "cisco_ios"
    cpu_command = "show processes cpu"
    use_enable = True
    parse_cpu_output = staticmethod(parse_cpu_output)


__all__ = ["CiscoIOSDriver", "parse_cpu_output"]
