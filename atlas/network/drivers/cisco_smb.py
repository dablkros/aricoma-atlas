"""Cisco Business/CBS CPU collection kept separate from IOS semantics."""

import re

from atlas.network.base import NetworkDriver
from atlas.network.errors import ParseFailedError
from atlas.prophylaxis.models import CPUUtilizationValues


CPU_PATTERN = re.compile(
    r"(?:^|\n)\s*five\s+seconds:\s*(?P<current>\d+(?:\.\d+)?)%\s*;\s*"
    r"one\s+minute:\s*(?P<one_minute>\d+(?:\.\d+)?)%\s*;\s*"
    r"five\s+minutes:\s*(?P<five_minutes>\d+(?:\.\d+)?)%\s*(?:$|\n)",
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


class CiscoSMBDriver(NetworkDriver):
    # Netmiko has no CBS-labelled driver; its Cisco S300 adapter provides the
    # compatible Cisco Small Business prompt/paging behavior used here.
    platform_device_type = "cisco_s300"
    cpu_command = "show cpu utilization"
    use_enable = True
    parse_cpu_output = staticmethod(parse_cpu_output)


__all__ = ["CiscoSMBDriver", "parse_cpu_output"]
