"""Platform-specific parsing kept separate from Ansible transport."""

import re

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import (
    CPUUtilizationValues,
    DeviceFactsValues,
    OSVersionValues,
    RawCollectorResult,
    UptimeValues,
    EvaluationOutcome,
    HardwareSensorValue,
    SystemMetricValues,
)


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


FAN_STATUS = {
    1: ("normal", EvaluationOutcome.PASS),
    2: ("warning", EvaluationOutcome.FAIL),
    3: ("critical", EvaluationOutcome.FAIL),
    4: ("shutdown", EvaluationOutcome.FAIL),
    5: ("notPresent", EvaluationOutcome.UNKNOWN),
    6: ("notFunctioning", EvaluationOutcome.FAIL),
}

# Cisco Business 350 data sheet, hardware table. Keep this deliberately
# model-specific: an unknown chassis must not inherit a fan expectation.
CBS_EXPECTED_FANS = {
    "CBS350-16T-2G": 0,
}


def _snmp_data(raw: RawCollectorResult) -> dict:
    if not isinstance(raw.data, dict):
        raise ParseFailedError()
    metrics = raw.data.get("metrics")
    if not isinstance(metrics, dict):
        raise ParseFailedError()
    return raw.data


def _identity(data: dict) -> dict:
    result = {}
    for source, target in (("model", "model"), ("sys_object_id", "sys_object_id")):
        value = data.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = " ".join(value.split())
    return result


def _metric(data: dict, name: str) -> dict:
    value = data["metrics"].get(name)
    if not isinstance(value, dict) or not isinstance(value.get("status"), str):
        raise ParseFailedError()
    return value


def normalize_snmp_cpu(raw: RawCollectorResult):
    data = _snmp_data(raw)
    if raw.platform == "aruba-aos-cx":
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNSUPPORTED,
            reason_code="aruba_aoscx_cpu_oid_unverified",
            **_identity(data),
        )
    values = []
    for name in ("cpu_one_second", "cpu_one_minute", "cpu_five_minutes"):
        item = _metric(data, name)
        if item["status"] in {"no_such_object", "no_such_instance", "unsupported"}:
            return SystemMetricValues(
                outcome=EvaluationOutcome.UNSUPPORTED,
                reason_code="metric_not_exposed",
                **_identity(data),
            )
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParseFailedError()
        values.append(float(value))
    try:
        return CPUUtilizationValues(
            current_percent=values[0],
            one_minute_percent=values[1],
            five_minute_percent=values[2],
        )
    except ValueError:
        raise ParseFailedError() from None


def normalize_snmp_memory(raw: RawCollectorResult) -> SystemMetricValues:
    data = _snmp_data(raw)
    return SystemMetricValues(
        outcome=EvaluationOutcome.UNSUPPORTED,
        reason_code=(
            "aruba_aoscx_memory_oid_unverified"
            if raw.platform == "aruba-aos-cx"
            else "cbs350_memory_oid_unverified"
        ),
        **_identity(data),
    )


def normalize_snmp_temperature(raw: RawCollectorResult) -> SystemMetricValues:
    data = _snmp_data(raw)
    if raw.platform == "aruba-aos-cx":
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNSUPPORTED,
            reason_code="aruba_aoscx_temperature_oid_unverified",
            **_identity(data),
        )
    item = _metric(data, "temperature")
    if item["status"] != "ok":
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNSUPPORTED,
            reason_code="metric_not_exposed",
            **_identity(data),
        )
    value = item.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseFailedError()
    # The tested OID returned 47, but the cited Cisco document does not state
    # a unit or threshold. Preserve the reading without inventing Celsius.
    return SystemMetricValues(
        outcome=EvaluationOutcome.UNKNOWN,
        value=float(value),
        reason_code="temperature_unit_unverified",
        **_identity(data),
    )


def normalize_snmp_fans(raw: RawCollectorResult) -> SystemMetricValues:
    data = _snmp_data(raw)
    if raw.platform == "aruba-aos-cx":
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNSUPPORTED,
            reason_code="aruba_aoscx_fan_oid_unverified",
            **_identity(data),
        )
    model = data.get("model")
    expected = CBS_EXPECTED_FANS.get(model) if isinstance(model, str) else None
    sensors = []
    for index in range(1, 4):
        item = _metric(data, f"fan_{index}")
        code = item.get("value") if item.get("status") == "ok" else None
        if isinstance(code, bool) or not isinstance(code, int):
            sensors.append(HardwareSensorValue(
                index=index,
                status_code=None,
                status_text="unknown",
                outcome=EvaluationOutcome.UNKNOWN,
            ))
            continue
        text, outcome = FAN_STATUS.get(
            code,
            ("unknown", EvaluationOutcome.UNKNOWN),
        )
        if expected == 0:
            outcome = (
                EvaluationOutcome.NOT_APPLICABLE
                if code == 5
                else EvaluationOutcome.UNKNOWN
            )
        elif code == 5 and expected is not None and index <= expected:
            outcome = EvaluationOutcome.FAIL
        sensors.append(HardwareSensorValue(
            index=index,
            status_code=code,
            status_text=text,
            outcome=outcome,
        ))
    outcomes = {sensor.outcome for sensor in sensors}
    if EvaluationOutcome.FAIL in outcomes:
        outcome = EvaluationOutcome.FAIL
        reason = "fan_failure_detected"
    elif expected == 0 and outcomes == {EvaluationOutcome.NOT_APPLICABLE}:
        outcome = EvaluationOutcome.NOT_APPLICABLE
        reason = "fanless_model"
    elif expected is not None and outcomes == {EvaluationOutcome.PASS}:
        outcome = EvaluationOutcome.PASS
        reason = None
    else:
        outcome = EvaluationOutcome.UNKNOWN
        reason = "fan_expectation_unverified"
    return SystemMetricValues(
        outcome=outcome,
        reason_code=reason,
        expected_components=expected,
        sensors=tuple(sensors),
        **_identity(data),
    )


def _device_facts(values: object) -> DeviceFactsValues:
    if not isinstance(values, dict):
        raise ParseFailedError()
    normalized = {}
    for key in ("hostname", "model", "os_version"):
        value = values.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ParseFailedError()
        normalized[key] = " ".join(value.split())
    serial_number = values.get("serial_number")
    if isinstance(serial_number, str) and serial_number.strip():
        normalized["serial_number"] = " ".join(serial_number.split())
    uptime_seconds = values.get("uptime_seconds")
    if (
        isinstance(uptime_seconds, int)
        and not isinstance(uptime_seconds, bool)
        and uptime_seconds >= 0
    ):
        normalized["uptime_seconds"] = uptime_seconds
    try:
        return DeviceFactsValues.model_validate(normalized)
    except ValueError:
        raise ParseFailedError() from None


def parse_structured_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    return _device_facts(raw.data)


def _duration_seconds(**parts: object) -> int:
    multipliers = {
        "years": 365 * 24 * 60 * 60,
        "weeks": 7 * 24 * 60 * 60,
        "days": 24 * 60 * 60,
        "hours": 60 * 60,
        "minutes": 60,
        "seconds": 1,
    }
    total = 0
    found = False
    for name, multiplier in multipliers.items():
        value = parts.get(name)
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            total += value * multiplier
            found = True
    if not found:
        raise ParseFailedError()
    return total


def _first_text(rows: object, key: str) -> str | None:
    if not isinstance(rows, list):
        return None
    return next(
        (
            value.strip()
            for row in rows
            if isinstance(row, dict)
            and isinstance((value := row.get(key)), str)
            and value.strip()
        ),
        None,
    )


def parse_cisco_ios_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    data = raw.data
    if isinstance(data, dict) and all(
        isinstance(data.get(key), str)
        for key in ("hostname", "model", "os_version")
    ):
        return _device_facts(data)
    if not isinstance(data, dict):
        raise ParseFailedError()
    version_output = data.get("show_version")
    inventory_output = data.get("show_inventory")
    if not isinstance(version_output, str) or not version_output.strip():
        raise ParseFailedError()
    if not isinstance(inventory_output, str):
        raise ParseFailedError()
    try:
        from ntc_templates.parse import parse_output

        version_rows = parse_output(
            platform="cisco_ios",
            command="show version",
            data=version_output,
        )
        inventory_rows = parse_output(
            platform="cisco_ios",
            command="show inventory",
            data=inventory_output,
        )
    except Exception:
        raise ParseFailedError() from None
    if not version_rows or not isinstance(version_rows[0], dict):
        raise ParseFailedError()
    version = version_rows[0]
    inventory_model = next(
        (
            row.get("pid")
            for row in inventory_rows
            if isinstance(row, dict)
            and isinstance(row.get("pid"), str)
            and row.get("pid").strip()
        ),
        None,
    )
    inventory_serial = _first_text(inventory_rows, "sn")
    hardware = version.get("hardware")
    version_model = (
        hardware[0]
        if isinstance(hardware, list)
        and hardware
        and isinstance(hardware[0], str)
        else None
    )
    version_serials = version.get("serial")
    version_serial = (
        version_serials[0]
        if isinstance(version_serials, list)
        and version_serials
        and isinstance(version_serials[0], str)
        and version_serials[0].strip()
        else None
    )
    serial_number = inventory_serial or version_serial
    if serial_number is None:
        raise ParseFailedError()
    uptime_seconds = _duration_seconds(
        years=version.get("uptime_years"),
        weeks=version.get("uptime_weeks"),
        days=version.get("uptime_days"),
        hours=version.get("uptime_hours"),
        minutes=version.get("uptime_minutes"),
    )
    return _device_facts(
        {
            "hostname": version.get("hostname"),
            "model": inventory_model or version_model,
            "os_version": version.get("version"),
            "serial_number": serial_number,
            "uptime_seconds": uptime_seconds,
        }
    )


def parse_cisco_cbs_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    data = raw.data
    if not isinstance(data, dict):
        raise ParseFailedError()
    try:
        from ntc_templates.parse import parse_output
    except ImportError:
        raise ParseFailedError() from None

    command_outputs = {}
    for key in ("show_system", "show_version", "show_inventory"):
        output = data.get(key)
        if not isinstance(output, str) or not output.strip():
            raise ParseFailedError()
        command_outputs[key] = output

    try:
        system_rows = parse_output(
            platform="cisco_s300",
            command="show system",
            data=command_outputs["show_system"],
        )
        version_rows = parse_output(
            platform="cisco_s300",
            command="show version",
            data=command_outputs["show_version"],
        )
    except Exception:
        raise ParseFailedError() from None
    if (
        not isinstance(system_rows, list)
        or not system_rows
        or not isinstance(system_rows[0], dict)
        or not isinstance(version_rows, list)
        or not version_rows
        or not isinstance(version_rows[0], dict)
    ):
        raise ParseFailedError()

    model_match = re.search(
        r"(?im)^\s*PID:\s*(?P<model>\S+)",
        command_outputs["show_inventory"],
    )
    if model_match is None:
        raise ParseFailedError()
    serial_match = re.search(
        r"(?im)^\s*PID:\s*\S+.*?\bSN:\s*(?P<serial>\S+)",
        command_outputs["show_inventory"],
    )
    uptime_match = re.fullmatch(
        r"\s*(?P<days>\d+)\s*,\s*(?P<hours>\d+):"
        r"(?P<minutes>\d+):(?P<seconds>\d+)\s*",
        str(system_rows[0].get("up_time") or ""),
    )
    if serial_match is None or uptime_match is None:
        raise ParseFailedError()
    return _device_facts(
        {
            "hostname": system_rows[0].get("hostname"),
            "model": model_match.group("model"),
            "os_version": version_rows[0].get("sw_version"),
            "serial_number": serial_match.group("serial"),
            "uptime_seconds": _duration_seconds(**uptime_match.groupdict()),
        }
    )


def parse_aruba_aoscx_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    data = raw.data
    if not isinstance(data, dict):
        text = raw.raw_output
        if not isinstance(text, str):
            raise ParseFailedError()
        legacy_patterns = {
            "hostname": r"(?im)^\s*(?:Hostname|System Name)\s*:\s*(\S.*?)\s*$",
            "model": r"(?im)^\s*(?:Product Name|Platform)\s*:\s*(\S.*?)\s*$",
            "os_version": (
                r"(?im)^\s*(?:Version|ArubaOS-CX Version)\s*:\s*(\S.*?)\s*$"
            ),
        }
        legacy_values = {}
        for key, pattern in legacy_patterns.items():
            match = re.search(pattern, text)
            if match is None:
                raise ParseFailedError()
            legacy_values[key] = match.group(1)
        return _device_facts(legacy_values)
    system_output = data.get("show_system")
    if not isinstance(system_output, str) or not system_output.strip():
        raise ParseFailedError()
    try:
        from ntc_templates.parse import parse_output

        rows = parse_output(
            platform="aruba_aoscx",
            command="show system",
            data=system_output,
        )
    except Exception:
        raise ParseFailedError() from None
    if not rows or not isinstance(rows[0], dict):
        raise ParseFailedError()
    values = rows[0]
    serial_number = values.get("serial")
    if not isinstance(serial_number, str) or not serial_number.strip():
        raise ParseFailedError()
    try:
        uptime_seconds = _duration_seconds(
            weeks=values.get("uptime_weeks"),
            days=values.get("uptime_days"),
            hours=values.get("uptime_hours"),
            minutes=values.get("uptime_minutes"),
        )
    except ParseFailedError:
        if re.search(r"(?im)^\s*Up Time\s*:\s*under a minute\s*$", system_output):
            uptime_seconds = 0
        else:
            raise
    return _device_facts(
        {
            "hostname": values.get("hostname"),
            "model": values.get("product"),
            "os_version": values.get("version"),
            "serial_number": serial_number,
            "uptime_seconds": uptime_seconds,
        }
    )


def parse_fortios_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    data = raw.data
    if not isinstance(data, dict):
        raise ParseFailedError()
    uptime_state = data.get("uptime_state")
    results = uptime_state.get("results") if isinstance(uptime_state, dict) else None
    if not isinstance(results, dict):
        return _device_facts(data)
    snapshot = results.get("snapshot_utc_time")
    reboot = results.get("utc_last_reboot")
    try:
        snapshot_value = int(snapshot)
        reboot_value = int(reboot)
    except (TypeError, ValueError):
        raise ParseFailedError() from None
    if reboot_value < 0 or snapshot_value < reboot_value:
        raise ParseFailedError()
    millisecond_epoch_threshold = 100_000_000_000
    snapshot_is_milliseconds = snapshot_value >= millisecond_epoch_threshold
    reboot_is_milliseconds = reboot_value >= millisecond_epoch_threshold
    if snapshot_is_milliseconds != reboot_is_milliseconds:
        raise ParseFailedError()
    uptime_seconds = snapshot_value - reboot_value
    if snapshot_is_milliseconds:
        uptime_seconds //= 1_000
    enriched = dict(data)
    enriched["uptime_seconds"] = uptime_seconds
    enriched.pop("uptime_state", None)
    return _device_facts(enriched)


DEVICE_FACTS_PARSERS = {
    "cisco-ios": parse_cisco_ios_device_facts,
    "cisco-ios-xe": parse_cisco_ios_device_facts,
    "cisco-cbs": parse_cisco_cbs_device_facts,
    "cisco-cbs250-350": parse_cisco_cbs_device_facts,
    "fortios": parse_fortios_device_facts,
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


def normalize_uptime(raw: RawCollectorResult) -> UptimeValues:
    facts = normalize_device_facts(raw)
    if facts.uptime_seconds is None:
        raise ParseFailedError()
    return UptimeValues(uptime_seconds=facts.uptime_seconds)


__all__ = [
    "normalize_cpu",
    "normalize_device_facts",
    "normalize_os_version",
    "normalize_uptime",
    "parse_aruba_aoscx_device_facts",
    "parse_cisco_cbs_device_facts",
    "parse_cisco_cbs_cpu",
    "parse_cisco_ios_device_facts",
    "parse_cisco_ios_cpu",
    "parse_fortios_cpu",
    "parse_fortios_device_facts",
]
