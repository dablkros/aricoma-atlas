"""Platform-specific parsing kept separate from Ansible transport."""

from pathlib import Path
import re

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import (
    CPUUtilizationValues,
    DeviceFactsValues,
    OSVersionValues,
    RawCollectorResult,
)


TEXTFSM_DIRECTORY = Path(__file__).resolve().parents[2] / "automation" / "textfsm"


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


def parse_cisco_ios_cpu(raw: RawCollectorResult) -> CPUUtilizationValues:
    return _parse_cisco_cpu(raw.raw_output, CISCO_IOS_CPU_PATTERN)


def parse_cisco_cbs_cpu(raw: RawCollectorResult) -> CPUUtilizationValues:
    return _parse_cisco_cpu(raw.raw_output, CISCO_CBS_CPU_PATTERN)


def parse_fortios_cpu(raw: RawCollectorResult) -> CPUUtilizationValues:
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
    "cisco-cbs250-350": parse_cisco_cbs_cpu,
    "fortios": parse_fortios_cpu,
}


def normalize_cpu(raw: RawCollectorResult) -> CPUUtilizationValues:
    try:
        parser = CPU_PARSERS[raw.platform]
    except KeyError:
        from atlas.prophylaxis.errors import UnsupportedPlatformError

        raise UnsupportedPlatformError() from None
    return parser(raw)


def _device_facts(values: object) -> DeviceFactsValues:
    if not isinstance(values, dict):
        raise ParseFailedError()
    normalized = {}
    for key in ("hostname", "model", "os_version"):
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ParseFailedError()
        normalized[key] = " ".join(value.split())
    try:
        return DeviceFactsValues.model_validate(normalized)
    except ValueError:
        raise ParseFailedError() from None


def parse_structured_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    return _device_facts(raw.data)


def parse_cisco_cbs_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    if not isinstance(raw.raw_output, str) or not raw.raw_output.strip():
        raise ParseFailedError()
    try:
        import textfsm
    except ImportError:
        raise ParseFailedError() from None
    try:
        template_path = TEXTFSM_DIRECTORY / "cisco_cbs_device_facts.textfsm"
        if template_path.is_symlink() or not template_path.is_file():
            raise ParseFailedError()
        with template_path.open(encoding="utf-8") as template_file:
            parser = textfsm.TextFSM(template_file)
            rows = parser.ParseText(raw.raw_output)
    except (OSError, textfsm.TextFSMError):
        raise ParseFailedError() from None
    if not rows:
        raise ParseFailedError()
    values = dict(zip((name.lower() for name in parser.header), rows[0]))
    return _device_facts(values)


def parse_aruba_aoscx_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    text = raw.raw_output
    if not isinstance(text, str):
        raise ParseFailedError()
    patterns = {
        "hostname": (
            r"(?im)^\s*Hostname\s*:\s*(\S.*?)\s*$",
            r"(?im)^\s*System Name\s*:\s*(\S.*?)\s*$",
        ),
        "model": (
            r"(?im)^\s*Product Name\s*:\s*(\S.*?)\s*$",
            r"(?im)^\s*Platform\s*:\s*(\S.*?)\s*$",
        ),
        "os_version": (
            r"(?im)^\s*Version\s*:\s*(\S.*?)\s*$",
            r"(?im)^\s*ArubaOS-CX\s+Version\s*:\s*(\S.*?)\s*$",
        ),
    }
    values = {}
    for key, candidates in patterns.items():
        match = next(
            (match for pattern in candidates if (match := re.search(pattern, text))),
            None,
        )
        if match is None:
            raise ParseFailedError()
        values[key] = match.group(1)
    return _device_facts(values)


DEVICE_FACTS_PARSERS = {
    "cisco-ios": parse_structured_device_facts,
    "cisco-ios-xe": parse_structured_device_facts,
    "cisco-cbs": parse_cisco_cbs_device_facts,
    "cisco-cbs250-350": parse_cisco_cbs_device_facts,
    "fortios": parse_structured_device_facts,
    "aruba-aos-cx": parse_aruba_aoscx_device_facts,
}


def normalize_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    try:
        parser = DEVICE_FACTS_PARSERS[raw.platform]
    except KeyError:
        from atlas.prophylaxis.errors import UnsupportedPlatformError

        raise UnsupportedPlatformError() from None
    return parser(raw)


def normalize_os_version(raw: RawCollectorResult) -> OSVersionValues:
    facts = normalize_device_facts(raw)
    return OSVersionValues(os_version=facts.os_version)


__all__ = [
    "normalize_cpu",
    "normalize_device_facts",
    "normalize_os_version",
    "parse_aruba_aoscx_device_facts",
    "parse_cisco_cbs_device_facts",
    "parse_cisco_cbs_cpu",
    "parse_cisco_ios_cpu",
    "parse_fortios_cpu",
]
