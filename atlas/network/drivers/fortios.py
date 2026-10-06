"""FortiGate/FortiOS CPU collection."""

import re

from atlas.network.base import NetworkDriver
from atlas.network.errors import ParseFailedError
from atlas.prophylaxis.models import CPUUtilizationValues


CPU_PATTERN = re.compile(
    r"CPU\s+states:\s*"
    r"(?P<user>\d+(?:\.\d+)?)%\s+user\s+"
    r"(?P<system>\d+(?:\.\d+)?)%\s+system\s+"
    r"(?P<nice>\d+(?:\.\d+)?)%\s+nice\s+"
    r"(?P<idle>\d+(?:\.\d+)?)%\s+idle\b",
    re.IGNORECASE,
)


def parse_cpu_output(raw_output: str) -> CPUUtilizationValues:
    match = CPU_PATTERN.search(raw_output) if isinstance(raw_output, str) else None
    if match is None:
        raise ParseFailedError()
    values = {name: float(value) for name, value in match.groupdict().items()}
    if any(value < 0 or value > 100 for value in values.values()):
        raise ParseFailedError()
    return CPUUtilizationValues(current_percent=100.0 - values["idle"])


class FortiOSDriver(NetworkDriver):
    platform_device_type = "fortinet"
    cpu_command = "get system performance status"
    parse_cpu_output = staticmethod(parse_cpu_output)


__all__ = ["FortiOSDriver", "parse_cpu_output"]
