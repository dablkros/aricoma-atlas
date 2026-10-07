"""Platform-specific parsing kept separate from Ansible transport."""

import re

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import CPUUtilizationValues, RawCheckResult


CISCO_IOS_CPU_PATTERN = re.compile(
    r"CPU\s+utilization\s+for\s+five\s+seconds:\s*"
    r"(?P<current>\d+(?:\.\d+)?)%\s*/\s*"
    r"(?P<interrupt>\d+(?:\.\d+)?)%\s*;\s*"
    r"one\s+minute:\s*(?P<one_minute>\d+(?:\.\d+)?)%\s*;\s*"
    r"five\s+minutes:\s*(?P<five_minutes>\d+(?:\.\d+)?)%",
    re.IGNORECASE,
)

CISCO_CBS_CPU_PATTERN = re.compile(
    r"(?:^|\n)\s*five\s+seconds:\s*(?P<current>\d+(?:\.\d+)?)%\s*;\s*"
    r"one\s+minute:\s*(?P<one_minute>\d+(?:\.\d+)?)%\s*;\s*"
    r"five\s+minutes:\s*(?P<five_minutes>\d+(?:\.\d+)?)%\s*(?:$|\n)",
    re.IGNORECASE,
)


def _parse_cisco_cpu(raw_output: object, pattern: re.Pattern) -> CPUUtilizationValues:
    match = pattern.search(raw_output) if isinstance(raw_output, str) else None
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


def parse_cisco_ios_cpu(raw: RawCheckResult) -> CPUUtilizationValues:
    return _parse_cisco_cpu(raw.raw_output, CISCO_IOS_CPU_PATTERN)


def parse_cisco_cbs_cpu(raw: RawCheckResult) -> CPUUtilizationValues:
    return _parse_cisco_cpu(raw.raw_output, CISCO_CBS_CPU_PATTERN)


def parse_fortios_cpu(raw: RawCheckResult) -> CPUUtilizationValues:
    data = raw.data
    if not isinstance(data, dict):
        raise ParseFailedError()
    cpu = data.get("cpu")
    if not isinstance(cpu, list) or not cpu or not isinstance(cpu[0], dict):
        raise ParseFailedError()
    current = cpu[0].get("current")
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ParseFailedError()

    one_minute = None
    historical = cpu[0].get("historical")
    if isinstance(historical, dict):
        minute = historical.get("1-min")
        if isinstance(minute, dict):
            average = minute.get("average")
            if isinstance(average, (int, float)) and not isinstance(average, bool):
                one_minute = float(average)

    try:
        return CPUUtilizationValues(
            current_percent=float(current),
            one_minute_percent=one_minute,
        )
    except ValueError:
        raise ParseFailedError() from None


CPU_PARSERS = {
    "cisco-ios": parse_cisco_ios_cpu,
    "cisco-ios-xe": parse_cisco_ios_cpu,
    "cisco-cbs": parse_cisco_cbs_cpu,
    "fortios": parse_fortios_cpu,
}


def normalize_cpu(raw: RawCheckResult) -> CPUUtilizationValues:
    try:
        parser = CPU_PARSERS[raw.platform]
    except KeyError:
        from atlas.prophylaxis.errors import UnsupportedPlatformError

        raise UnsupportedPlatformError() from None
    return parser(raw)


__all__ = [
    "normalize_cpu",
    "parse_cisco_cbs_cpu",
    "parse_cisco_ios_cpu",
    "parse_fortios_cpu",
]
