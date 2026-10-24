"""Platform-specific parsing kept separate from Ansible transport."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import (
    CPUUtilizationValues,
    DeviceFactsValues,
    DiagnosticValues,
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
    if data.get("source_transport") == "ssh":
        output = data.get("show_performance_status")
        match = (
            re.search(
                r"(?im)^\s*CPU\s+states:\s*.*?"
                r"(?P<idle>\d+(?:\.\d+)?)%\s+idle\b",
                output,
            )
            if isinstance(output, str)
            else None
        )
        if match is None:
            raise ParseFailedError()
        current = 100.0 - float(match.group("idle"))
        try:
            return CPUUtilizationValues(current_percent=current)
        except ValueError:
            raise ParseFailedError() from None
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
    "CBS350-12XT": 2,
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
        elif expected is not None:
            if code == 5 and index <= expected:
                outcome = EvaluationOutcome.FAIL
            elif code == 5 and index > expected:
                outcome = EvaluationOutcome.NOT_APPLICABLE
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
    elif (
        expected is not None
        and all(
            sensor.outcome == EvaluationOutcome.PASS
            for sensor in sensors[:expected]
        )
        and all(
            sensor.outcome == EvaluationOutcome.NOT_APPLICABLE
            for sensor in sensors[expected:]
        )
    ):
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


INVALID_COMMAND_PATTERNS = (
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"invalid input",
        r"unrecognized command",
        r"unknown command",
        r"incomplete command",
        r"ambiguous command",
        r"bad command",
        r"command not found",
        r"(?m)^\s*Unknown action\s+\d+\s*$",
        r"(?m)^\s*Command fail\.\s+Return code\s+-?\d+\s*$",
        r"(?m)^\s*%.*not supported\s*$",
    )
)
INVALID_COMMAND_PATTERNS = tuple(INVALID_COMMAND_PATTERNS)
CERTIFICATE_WARNING_DAYS = 60
CERTIFICATE_CRITICAL_DAYS = 30


def _command_outputs(raw: RawCollectorResult) -> dict[str, str]:
    if not isinstance(raw.data, dict):
        raise ParseFailedError()
    outputs = {
        key: value
        for key, value in raw.data.items()
        if isinstance(key, str)
        and isinstance(value, str)
        and value.strip()
    }
    if not outputs:
        raise ParseFailedError()
    return outputs


def _json_outputs(raw: RawCollectorResult) -> dict[str, object]:
    parsed = {}
    for key, value in _command_outputs(raw).items():
        if not value.lstrip().startswith(("{", "[")):
            continue
        try:
            parsed[key] = json.loads(value)
        except (TypeError, ValueError):
            continue
    return parsed


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _usable_outputs(raw: RawCollectorResult) -> dict[str, str]:
    return {
        key: value
        for key, value in _command_outputs(raw).items()
        if not any(pattern.search(value) for pattern in INVALID_COMMAND_PATTERNS)
    }


def _commands(outputs: dict[str, str]) -> tuple[str, ...]:
    return tuple(key.replace("_", " ") for key in outputs)


def _diagnostic(
    outcome: EvaluationOutcome,
    summary: str,
    *,
    reason_code: str | None = None,
    metrics=None,
    affected_items=(),
    records=(),
    outputs=None,
) -> DiagnosticValues:
    return DiagnosticValues(
        outcome=outcome,
        reason_code=reason_code,
        summary=summary,
        metrics=metrics or {},
        affected_items=tuple(affected_items),
        records=tuple(records),
        source_commands=_commands(outputs or {}),
    )


def normalize_command_cpu(raw: RawCollectorResult) -> CPUUtilizationValues:
    outputs = _usable_outputs(raw)
    key = (
        "show_processes_cpu"
        if raw.platform in {"cisco-ios", "cisco-ios-xe"}
        else "show_cpu_utilization"
    )
    output = outputs.get(key)
    if output is None:
        raise ParseFailedError()
    return normalize_cpu(raw.model_copy(update={"data": None, "raw_output": output}))


def normalize_memory(raw: RawCollectorResult) -> SystemMetricValues:
    outputs = _usable_outputs(raw)
    text = "\n".join(outputs.values())
    for payload in _json_outputs(raw).values():
        for item in _walk_json(payload):
            memory = next((
                value for key, value in item.items()
                if "memory" in str(key).casefold()
            ), None)
            if memory is None:
                continue
            for memory_item in _walk_json(memory):
                current = memory_item.get("current")
                if (
                    isinstance(current, (int, float))
                    and not isinstance(current, bool)
                    and 0 <= float(current) <= 100
                ):
                    return SystemMetricValues(
                        outcome=EvaluationOutcome.UNKNOWN,
                        value=float(current),
                        unit="percent",
                        reason_code="memory_threshold_unconfigured",
                    )
    patterns = (
        re.compile(
            r"Processor\s+Pool\s+Total:\s*(?P<total>\d+)\s+"
            r"Used:\s*(?P<used>\d+)\s+Free:\s*(?P<free>\d+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:total\s+memory|memory\s+total)\s*[:=]\s*"
            r"(?P<total>\d+)\D+?(?:used\s+memory|memory\s+used|used)"
            r"\s*[:=]\s*(?P<used>\d+)",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"Memory:\s*(?P<total>\d+)k\s+total,\s*"
            r"(?P<used>\d+)k\s+used\s*\(\d+(?:\.\d+)?%\)",
            re.IGNORECASE,
        ),
    )
    match = None
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            break
    if match is None:
        return SystemMetricValues(
            outcome=(
                EvaluationOutcome.UNKNOWN
                if outputs
                else EvaluationOutcome.UNSUPPORTED
            ),
            reason_code=(
                "memory_format_unverified"
                if outputs
                else "memory_command_unsupported"
            ),
        )
    total = int(match.group("total"))
    used = int(match.group("used"))
    if total <= 0 or used < 0 or used > total:
        raise ParseFailedError()
    return SystemMetricValues(
        outcome=EvaluationOutcome.UNKNOWN,
        value=round(used * 100 / total, 2),
        unit="percent",
        reason_code="memory_threshold_unconfigured",
    )


def _environment_outputs(raw: RawCollectorResult) -> dict[str, str]:
    return {
        key: value
        for key, value in _usable_outputs(raw).items()
        if any(
            token in key
            for token in (
                "environment", "power_inline", "post", "system_resources",
                "sensor_status", "session_banner", "deviceinfo_psu",
                "deviceinfo_tpm",
            )
        )
    }


def _fortios_is_vm(raw: RawCollectorResult) -> bool:
    if raw.platform != "fortios":
        return False
    status = _usable_outputs(raw).get("get_system_status", "")
    if re.search(r"(?im)^\s*Version:\s*FortiGate-VM", status):
        return True
    return any(
        isinstance(value, str) and "fortigate-vm" in value.casefold()
        for payload in _json_outputs(raw).values()
        for item in _walk_json(payload)
        for value in item.values()
    )


def normalize_environment_health(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _environment_outputs(raw)
    if not outputs:
        if _fortios_is_vm(raw):
            return _diagnostic(
                EvaluationOutcome.NOT_APPLICABLE,
                "Hardware environment sensors do not apply to this FortiGate VM",
                reason_code="virtual_appliance_has_no_hardware_sensors",
            )
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported environment command returned data",
            reason_code="environment_command_unsupported",
        )
    bad = []
    warnings = []
    failure_pattern = re.compile(
        r"\b(?:critical|shutdown|over[- ]?temperature|faulty|fail(?:ed|ure)?|"
        r"not functioning|err-disabled)\b",
        re.IGNORECASE,
    )
    warning_pattern = re.compile(r"\bwarning\b", re.IGNORECASE)
    for value in outputs.values():
        bad.extend(
            " ".join(line.split())[:160]
            for line in value.splitlines()
            if failure_pattern.search(line)
            and not re.search(r"\b(?:0|no)\s+(?:failures?|faults?)\b", line, re.I)
        )
        warnings.extend(
            " ".join(line.split())[:160]
            for line in value.splitlines()
            if warning_pattern.search(line) and not failure_pattern.search(line)
        )
    if bad:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            f"Environment output contains {len(bad)} explicit fault states",
            reason_code="environment_fault_detected",
            metrics={"fault_count": len(bad)},
            affected_items=bad[:20],
            outputs=outputs,
        )
    if warnings:
        return _diagnostic(
            EvaluationOutcome.WARNING,
            f"Environment output contains {len(warnings)} warning states",
            reason_code="environment_warning_detected",
            metrics={"warning_count": len(warnings)},
            affected_items=warnings[:20],
            outputs=outputs,
        )
    healthy = sum(
        1
        for value in outputs.values()
        for line in value.splitlines()
        if re.search(r"\b(?:ok|normal|active|passed|good)\b", line, re.I)
    )
    if not healthy:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN,
            "Environment output was collected but no known state was parsed",
            reason_code="environment_state_unrecognized",
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.PASS,
        "Environment commands returned no explicit fault state",
        metrics={"fault_count": 0, "healthy_markers": healthy},
        outputs=outputs,
    )


def normalize_temperature(raw: RawCollectorResult) -> SystemMetricValues:
    outputs = _environment_outputs(raw)
    text = "\n".join(outputs.values())
    values = [
        float(value)
        for value in re.findall(
            r"(?i)temperature[^\n\d-]{0,40}(-?\d+(?:\.\d+)?)\s*(?:c|celsius)\b",
            text,
        )
    ]
    if raw.platform == "fortios":
        for payload in _json_outputs(raw).values():
            for item in _walk_json(payload):
                for key, value in item.items():
                    if (
                        "temp" in str(key).casefold()
                        and isinstance(value, (int, float))
                        and not isinstance(value, bool)
                    ):
                        values.append(float(value))
        for output in outputs.values():
            blocks = re.split(r"(?m)(?=^\S[^:\n]*:\s*[^\n]*$)", output)
            for block in blocks:
                if not re.search(r"(?i)\b(?:temp|temperature|thermal)\b", block):
                    continue
                match = re.search(
                    r"(?im)^\s*Value:\s*(-?\d+(?:\.\d+)?)\s*°?C\b",
                    block,
                )
                if match:
                    values.append(float(match.group(1)))
    if values:
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNKNOWN,
            value=max(values),
            unit="celsius",
            reason_code="temperature_threshold_unconfigured",
        )
    if _fortios_is_vm(raw):
        return SystemMetricValues(
            outcome=EvaluationOutcome.NOT_APPLICABLE,
            reason_code="virtual_appliance_has_no_cpu_temperature_sensor",
        )
    return SystemMetricValues(
        outcome=(EvaluationOutcome.UNKNOWN if outputs else EvaluationOutcome.UNSUPPORTED),
        reason_code=(
            "temperature_value_unavailable"
            if outputs
            else "temperature_command_unsupported"
        ),
    )


def normalize_fan_health(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _environment_outputs(raw)
    fan_lines = [
        " ".join(line.split())[:160]
        for value in outputs.values()
        for line in value.splitlines()
        if re.search(r"\bfan\b", line, re.IGNORECASE)
    ]
    if not fan_lines:
        if _fortios_is_vm(raw):
            return _diagnostic(
                EvaluationOutcome.NOT_APPLICABLE,
                "Fan sensors do not apply to this FortiGate VM",
                reason_code="virtual_appliance_has_no_fans",
            )
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No fan status was exposed by supported commands",
            reason_code="fan_status_unavailable",
            outputs=outputs,
        )
    failed = [
        line
        for line in fan_lines
        if re.search(
            r"\b(?:fail(?:ed|ure)?|faulty|critical|shutdown|not functioning)\b",
            line,
            re.IGNORECASE,
        )
    ]
    if failed:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            f"{len(failed)} fan entries report a fault",
            reason_code="fan_failure_detected",
            metrics={"fan_lines": len(fan_lines), "fault_count": len(failed)},
            affected_items=failed,
            outputs=outputs,
        )
    healthy = [
        line
        for line in fan_lines
        if re.search(r"\b(?:ok|normal|active|functioning)\b", line, re.I)
    ]
    if not healthy:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN,
            "Fan output was collected but no known healthy state was parsed",
            reason_code="fan_state_unrecognized",
            metrics={"fan_lines": len(fan_lines)},
            affected_items=fan_lines[:20],
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.PASS,
        "Fan entries contain no explicit fault state",
        metrics={"fan_lines": len(fan_lines), "fault_count": 0},
        outputs=outputs,
    )


def _interface_outputs(raw: RawCollectorResult) -> dict[str, str]:
    return {
        key: value
        for key, value in _usable_outputs(raw).items()
        if any(
            token in key
            for token in ("interface", "interfaces", "deviceinfo_nic")
        )
    }


def normalize_interface_state(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _interface_outputs(raw)
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported interface-state command returned data",
            reason_code="interface_state_unsupported",
        )
    bad = []
    down = []
    up = []
    for value in outputs.values():
        current_interface = None
        for line in value.splitlines():
            normalized = " ".join(line.split())
            if raw.platform == "fortios":
                interface_match = re.match(r"^==\[([^]]+)]$", normalized)
                nic_match = re.match(
                    r"^===== diagnose hardware deviceinfo nic (\S+) =====$",
                    normalized,
                )
                if interface_match:
                    current_interface = interface_match.group(1)
                    continue
                if nic_match:
                    current_interface = nic_match.group(1)
                    continue
            if re.search(r"\berr-disabled\b", normalized, re.I):
                bad.append(normalized[:160])
            elif raw.platform == "fortios" and re.search(
                r"\b(?:status|link)\s*[:=]\s*down\b", normalized, re.I
            ):
                down.append(
                    f"{current_interface}: down"[:160]
                    if current_interface
                    else normalized[:160]
                )
            elif raw.platform == "fortios" and re.search(
                r"\b(?:status|link)\s*[:=]\s*up\b", normalized, re.I
            ):
                up.append(
                    f"{current_interface}: up"[:160]
                    if current_interface
                    else normalized[:160]
                )
            elif re.search(
                r"\b(?:administratively down|notconnect|down/down)\b",
                normalized,
                re.I,
            ):
                down.append(normalized[:160])
    for payload in _json_outputs(raw).values():
        for item in _walk_json(payload):
            name = next((
                item.get(key) for key in ("name", "interface", "interface_name", "port")
                if isinstance(item.get(key), str) and item.get(key)
            ), None)
            state = next((
                item.get(key) for key in ("status", "link", "state", "link_status")
                if isinstance(item.get(key), str)
            ), None)
            if not name or not state:
                continue
            if state.casefold() in {"up", "online", "connected"}:
                up.append(f"{name}: up"[:160])
            elif state.casefold() in {"down", "offline", "disconnected"}:
                down.append(f"{name}: down"[:160])
    up = list(dict.fromkeys(up))
    down = list(dict.fromkeys(down))
    discovered_count = raw.data.get("discovered_interface_count")
    diagnostics_truncated = raw.data.get("interface_diagnostics_truncated")
    inventory_metrics = {
        "err_disabled": 0,
        "up": len(up),
        "down_or_unused": len(down),
        "discovered_physical_interfaces": (
            discovered_count
            if isinstance(discovered_count, int)
            and not isinstance(discovered_count, bool)
            else None
        ),
        "interface_diagnostics_truncated": (
            diagnostics_truncated
            if isinstance(diagnostics_truncated, bool)
            else None
        ),
    }
    if bad:
        inventory_metrics["err_disabled"] = len(bad)
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "Interface inventory contains an explicit error-disabled state",
            reason_code="interface_err_disabled",
            metrics=inventory_metrics,
            affected_items=bad,
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Interface inventory and current link states were collected",
        reason_code="automatic_evaluation_not_configured",
        metrics=inventory_metrics,
        affected_items=tuple(dict.fromkeys(up + down))[:200],
        outputs=outputs,
    )


def normalize_interface_utilization(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _interface_outputs(raw)
    text = outputs.get("show_interfaces", "")
    percentages = []
    current = None
    bandwidth = None
    for line in text.splitlines():
        header = re.match(r"^(\S+) is ", line)
        if header:
            current = header.group(1)
            bandwidth = None
        bw = re.search(r"\bBW\s+(\d+)\s+Kbit", line, re.I)
        if bw:
            bandwidth = int(bw.group(1)) * 1000
        rate = re.search(r"5 minute (?:input|output) rate\s+(\d+)\s+bits/sec", line, re.I)
        if rate and current and bandwidth:
            percentages.append((current, int(rate.group(1)) * 100 / bandwidth))
    if not percentages:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN if outputs else EvaluationOutcome.UNSUPPORTED,
            "Interface utilization could not be normalized from one CLI sample",
            reason_code=(
                "interface_rate_unavailable"
                if outputs
                else "interface_utilization_unsupported"
            ),
            outputs=outputs,
        )
    interface, maximum = max(percentages, key=lambda item: item[1])
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Five-minute interface rates collected; alert threshold is not configured",
        reason_code="interface_threshold_unconfigured",
        metrics={
            "interfaces_with_rate": len({name for name, _ in percentages}),
            "maximum_utilization_percent": round(maximum, 2),
            "maximum_utilization_interface": interface,
        },
        outputs=outputs,
    )


def normalize_interface_errors(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _interface_outputs(raw)
    text = "\n".join(outputs.values())
    counters = [
        int(value)
        for value in re.findall(
            r"(?im)\b(\d+)\s+(?:input errors|CRC|frame|overrun|ignored|"
            r"output errors|collisions|interface resets)\b",
            text,
        )
    ]
    if raw.platform == "fortios":
        counters.extend(
            int(value)
            for value in re.findall(
                r"(?im)\b(?:rx|tx)[\s_-]*(?:errors?|crc|drops?)\s*[:=]\s*(\d+)",
                text,
            )
        )
        for payload in _json_outputs(raw).values():
            for item in _walk_json(payload):
                counters.extend(
                    int(value)
                    for key, value in item.items()
                    if any(token in str(key).casefold() for token in ("error", "drop", "crc"))
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                )
    if not counters:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN if outputs else EvaluationOutcome.UNSUPPORTED,
            "No interface error counters could be normalized",
            reason_code=(
                "interface_error_format_unverified"
                if outputs
                else "interface_error_counters_unsupported"
            ),
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Absolute interface counters collected; an interval delta is required",
        reason_code="counter_delta_unavailable",
        metrics={
            "reported_counters": len(counters),
            "absolute_error_total": sum(counters),
        },
        outputs=outputs,
    )


def normalize_license_state(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if "license" in key or (raw.platform == "fortios" and "system_status" in key)
    }
    text = "\n".join(outputs.values())
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported license command returned data",
            reason_code="license_command_unsupported",
        )
    failure = re.findall(
        r"(?im)^.*\b(?:out of compliance|expired|not authorized|evaluation expired|"
        r"authorization failed)\b.*$",
        text,
    )
    if raw.platform == "fortios":
        failure.extend(
            re.findall(
                r"(?im)^\s*License\s+Status:\s*(?:invalid|expired|unlicensed)\b.*$",
                text,
            )
        )
    if failure:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            f"{len(failure)} license states require action",
            reason_code="license_problem_detected",
            metrics={"problem_count": len(failure)},
            affected_items=(" ".join(line.split())[:160] for line in failure[:20]),
            outputs=outputs,
        )
    healthy = re.findall(
        r"(?i)\b(?:authorized|registered|compliance status:\s*in compliance)\b",
        text,
    )
    healthy.extend(
        line
        for line in text.splitlines()
        if re.search(r"\bin use\b", line, re.I)
        and not re.search(r"\bnot\s+in use\b", line, re.I)
    )
    if raw.platform == "fortios":
        healthy.extend(
            re.findall(
                r"(?im)^\s*License\s+Status:\s*(?:valid|licensed|active)\b.*$",
                text,
            )
        )
    if healthy:
        return _diagnostic(
            EvaluationOutcome.PASS,
            "License output contains an authorized or in-use state",
            metrics={"healthy_markers": len(healthy)},
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "License output was collected but its state is not recognized",
        reason_code="license_state_unrecognized",
        outputs=outputs,
    )


def _parse_certificate_date(value: str) -> datetime | None:
    normalized = " ".join(value.replace(",", " ").split())
    try:
        iso = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        iso = None
    if iso is not None:
        return (
            iso.astimezone(timezone.utc)
            if iso.tzinfo
            else iso.replace(tzinfo=timezone.utc)
        )
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is not None:
        return (
            parsed.astimezone(timezone.utc)
            if parsed.tzinfo
            else parsed.replace(tzinfo=timezone.utc)
        )
    for fmt in (
        "%H:%M:%S %Z %b %d %Y",
        "%b %d %H:%M:%S %Y %Z",
        "%b %d %Y %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S %Z",
    ):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_pki_certificates(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if "crypto" in key or "certificate" in key
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No supported certificate command returned certificate data",
            reason_code="pki_not_configured_or_unsupported",
        )
    text = "\n".join(outputs.values())
    if re.search(
        r"(?i)\b(?:no certificates? (?:found|configured)|certificate not found)\b",
        text,
    ):
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No locally configured certificate was reported",
            reason_code="pki_not_configured",
            outputs=outputs,
        )
    candidates = []
    for value in outputs.values():
        candidates.extend(
            match.group("date")
            for match in re.finditer(
                r"(?im)^\s*(?:end date|not after|valid until|valid to)\s*:\s*"
                r"(?P<date>.+?)\s*$",
                value,
            )
        )
    for payload in _json_outputs(raw).values():
        for item in _walk_json(payload):
            candidates.extend(
                value
                for key, value in item.items()
                if any(token in str(key).casefold() for token in ("not_after", "valid_to", "end_date", "expiry", "expires"))
                and isinstance(value, str)
            )
    dates = [parsed for value in candidates if (parsed := _parse_certificate_date(value))]
    if not dates:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN,
            "Certificate output was collected but expiration dates were not parsed",
            reason_code="certificate_date_unrecognized",
            outputs=outputs,
        )
    now = datetime.now(timezone.utc)
    days = [int((value - now).total_seconds() // 86400) for value in dates]
    minimum = min(days)
    if raw.platform != "fortios":
        if minimum < 0:
            outcome = EvaluationOutcome.FAIL
            reason = "certificate_expired"
        elif minimum <= CERTIFICATE_CRITICAL_DAYS:
            outcome = EvaluationOutcome.FAIL
            reason = "certificate_renewal_urgent"
        elif minimum <= CERTIFICATE_WARNING_DAYS:
            outcome = EvaluationOutcome.WARNING
            reason = "certificate_renewal_due"
        else:
            outcome = EvaluationOutcome.PASS
            reason = None
        return _diagnostic(
            outcome,
            f"Parsed {len(dates)} certificate expiration dates",
            reason_code=reason,
            metrics={
                "certificate_count": len(dates),
                "minimum_days_remaining": minimum,
                "warning_threshold_days": CERTIFICATE_WARNING_DAYS,
                "critical_threshold_days": CERTIFICATE_CRITICAL_DAYS,
                "automatic_renewal_state": "unverified",
            },
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        f"Parsed {len(dates)} certificate expiration dates",
        reason_code="automatic_evaluation_not_configured",
        metrics={
            "certificate_count": len(dates),
            "minimum_days_remaining": minimum,
            "expired_certificate_count": sum(day < 0 for day in days),
            "automatic_renewal_state": "unverified",
        },
        outputs=outputs,
    )


def _installed_version(outputs: dict[str, str]) -> str | None:
    text = outputs.get("show_version", "") or outputs.get("get_system_status", "")
    for pattern in (
        r"(?im)^\s*Version:\s*FortiGate\S*\s+v([^,\s]+)",
        r"(?i)\bVersion\s*:\s*(\S+)",
        r"(?i)\bVersion\s+([^,\s]+)",
        r"(?i)\bActive-image\b.*?\bVersion\s*:\s*(\S+)",
    ):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1)
    return None


def normalize_firmware_recommendation(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _usable_outputs(raw)
    version = _installed_version(outputs)
    if version is None:
        version = next((
            str(item[key])
            for payload in _json_outputs(raw).values()
            for item in _walk_json(payload)
            for key in ("version", "firmware_version", "build")
            if isinstance(item.get(key), (str, int))
        ), None)
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Installed firmware collected; no reviewed recommendation catalog is configured",
        reason_code="firmware_catalog_unavailable",
        metrics={"installed_version": version},
        outputs={
            key: value for key, value in outputs.items()
            if key in {"show_version", "get_system_status"}
        },
    )


def normalize_eol_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _usable_outputs(raw)
    inventory = outputs.get("show_inventory", "")
    product_ids = tuple(dict.fromkeys(re.findall(r"(?im)\bPID:\s*(\S+)", inventory)))
    fortios_status = outputs.get("get_system_status", "")
    fortios_model = None
    if raw.platform == "fortios":
        match = re.search(
            r"(?im)^\s*Version:\s*(FortiGate\S*)\s+v",
            fortios_status,
        )
        fortios_model = match.group(1) if match else None
        if fortios_model is None:
            fortios_model = next((
                str(item[key])
                for payload in _json_outputs(raw).values()
                for item in _walk_json(payload)
                for key in ("model_number", "model", "platform")
                if isinstance(item.get(key), str) and item.get(key)
            ), None)
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Product identifiers collected; no reviewed lifecycle record is configured",
        reason_code="lifecycle_catalog_unavailable",
        metrics={
            "product_id_count": len(product_ids),
            "primary_product_id": product_ids[0] if product_ids else None,
            "model": fortios_model,
        },
        outputs={
            key: value for key, value in outputs.items()
            if key in {"show_inventory", "get_system_status"}
        },
    )


def normalize_ntp_status(raw: RawCollectorResult) -> DiagnosticValues:
    command_outputs = _command_outputs(raw)
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if "ntp" in key or "sntp" in key or key == "show_clock"
    }
    text = "\n".join(outputs.values())
    if not outputs:
        unavailable = command_outputs.get("diagnose_sys_ntp_status", "")
        if raw.platform == "fortios" and re.search(
            r"(?im)^\s*Unknown action\s+\d+\s*$",
            unavailable,
        ):
            return _diagnostic(
                EvaluationOutcome.UNKNOWN,
                "NTP status requires diagnose access in the FortiOS admin profile",
                reason_code="fortios_diagnose_access_disabled",
            )
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported NTP/SNTP command returned data",
            reason_code="ntp_command_unsupported",
        )
    if re.search(
        r"(?i)\b(?:unsynchronized|not synchronized|not synced|synchronized:\s*no)\b",
        text,
    ):
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "Device clock is not synchronized",
            reason_code="ntp_unsynchronized",
            outputs=outputs,
        )
    structured_states = []
    for payload in _json_outputs(raw).values():
        for item in _walk_json(payload):
            for key, value in item.items():
                if any(token in str(key).casefold() for token in ("sync", "status", "state")):
                    structured_states.append(value)
    if any(value is False for value in structured_states) or any(
        isinstance(value, str) and value.casefold() in {"down", "unsynchronized", "not synchronized", "failed"}
        for value in structured_states
    ):
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "Device clock is not synchronized",
            reason_code="ntp_unsynchronized",
            outputs=outputs,
        )
    if any(value is True for value in structured_states) or any(
        isinstance(value, str) and value.casefold() in {"up", "synchronized", "synced", "success"}
        for value in structured_states
    ):
        return _diagnostic(
            EvaluationOutcome.PASS,
            "Device clock reports synchronized state",
            outputs=outputs,
        )
    if re.search(
        r"(?i)\b(?:clock is synchronized|synchronized, stratum|status:\s*up|"
        r"synchronized:\s*yes)\b",
        text,
    ):
        return _diagnostic(
            EvaluationOutcome.PASS,
            "Device clock reports synchronized state",
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "NTP/SNTP output was collected but synchronization state is unrecognized",
        reason_code="ntp_state_unrecognized",
        outputs=outputs,
    )


def normalize_recent_logs(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if (
            "logging" in key
            or "logfile" in key
            or key == "log_event"
            or "crash" in key
            or (raw.platform == "fortios" and key == "execute_log_display")
        )
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported log command returned data",
            reason_code="log_command_unsupported",
        )
    if re.search(
        r"(?i)\b(?:syslog|logging)\s+(?:is\s+)?(?:disabled|off)\b",
        "\n".join(outputs.values()),
    ):
        return _diagnostic(
            EvaluationOutcome.WARNING,
            "Local logging is disabled",
            reason_code="logging_disabled",
            outputs=outputs,
        )
    if raw.platform == "fortios":
        events = []
        for output in outputs.values():
            for line in output.splitlines():
                level_match = re.search(
                    r'(?i)\blevel="?([a-z]+)"?', line
                )
                if level_match:
                    events.append((level_match.group(1).casefold(), line))
        levels = [level for level, _line in events]
        critical_levels = {"emergency", "alert", "critical", "error"}
        warning_levels = {"warning"}
        critical = sum(level in critical_levels for level in levels)
        warnings = sum(level in warning_levels for level in levels)
        affected = []
        for level, line in events:
            if level not in critical_levels | warning_levels:
                continue
            date = re.search(r'\bdate="?([^"\s]+)"?', line)
            event_time = re.search(r'\btime="?([^"\s]+)"?', line)
            description = re.search(r'\blogdesc="([^"]+)"', line)
            message = re.search(r'\bmsg="([^"]+)"', line)
            details = []
            for match in (description, message):
                if match and match.group(1) not in details:
                    details.append(match.group(1))
            timestamp = " ".join(
                match.group(1)
                for match in (date, event_time)
                if match
            )
            prefix = f"{timestamp} " if timestamp else ""
            detail = " — ".join(details) or "FortiOS event"
            affected.append(f"{prefix}{level.upper()}: {detail}"[:240])
        affected = list(dict.fromkeys(affected))
        if critical or warnings:
            outcome = EvaluationOutcome.WARNING
            reason = "important_log_events_detected"
        elif levels:
            outcome = EvaluationOutcome.PASS
            reason = None
        else:
            outcome = EvaluationOutcome.UNKNOWN
            reason = "log_format_unrecognized"
        return _diagnostic(
            outcome,
            (
                f"Parsed {len(levels)} FortiOS event log entries; "
                f"{critical + warnings} require review"
                if critical or warnings
                else f"Parsed {len(levels)} FortiOS event log entries"
            ),
            reason_code=reason,
            metrics={
                "event_count": len(levels),
                "critical_or_error_count": critical,
                "warning_count": warnings,
                "important_event_count": critical + warnings,
                **{
                    f"severity_{level}_count": levels.count(level)
                    for level in (
                        "emergency",
                        "alert",
                        "critical",
                        "error",
                        "warning",
                        "notice",
                        "information",
                        "debug",
                    )
                    if level in levels
                },
            },
            affected_items=affected[:20],
            outputs=outputs,
        )
    severities = [
        int(value)
        for output in outputs.values()
        for value in re.findall(r"%[A-Z0-9_]+-(\d)-[A-Z0-9_]+", output)
    ]
    critical = sum(value <= 3 for value in severities)
    warnings = sum(value == 4 for value in severities)
    mnemonics = tuple(dict.fromkeys(
        match.group(0)[:128]
        for output in outputs.values()
        for match in re.finditer(r"%[A-Z0-9_]+-[0-4]-[A-Z0-9_]+", output)
    ))
    crash_artifacts = tuple(
        " ".join(line.split())[:160]
        for key, output in outputs.items()
        if "crash" in key
        for line in output.splitlines()
        if line.strip()
    )
    if critical:
        outcome = EvaluationOutcome.FAIL
        reason = "critical_log_events_detected"
    elif warnings or crash_artifacts:
        outcome = EvaluationOutcome.WARNING
        reason = (
            "crash_artifact_detected"
            if crash_artifacts
            else "warning_log_events_detected"
        )
    elif severities:
        outcome = EvaluationOutcome.PASS
        reason = None
    else:
        outcome = EvaluationOutcome.UNKNOWN
        reason = "log_format_unrecognized"
    return _diagnostic(
        outcome,
        f"Parsed {len(severities)} Cisco syslog events",
        reason_code=reason,
        metrics={
            "event_count": len(severities),
            "critical_or_error_count": critical,
            "warning_count": warnings,
            "crash_artifact_count": len(crash_artifacts),
        },
        affected_items=(mnemonics + crash_artifacts)[:20],
        outputs=outputs,
    )


def normalize_routing_table(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if key in {
            "show_ip_route", "show_route", "get_router_info_routing_table_all",
            "get_router_info_bgp_network", "router_ipv4",
        }
    }
    text = "\n".join(outputs.values())
    if not outputs or re.search(r"(?i)\bIP routing (?:is )?not enabled\b", text):
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No applicable routing table was returned",
            reason_code="routing_not_enabled_or_unsupported",
            outputs=outputs,
        )
    routes = re.findall(
        r"(?im)^\s*(?:[A-Z*][A-Z0-9* ]*\s+)?(?:\d{1,3}\.){3}\d{1,3}/\d+",
        text,
    )
    default = bool(re.search(r"(?im)\b0\.0\.0\.0/0\b|gateway of last resort", text))
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Routing table inventory collected",
        reason_code="automatic_evaluation_not_configured",
        metrics={"parsed_routes": len(routes), "default_route_present": default},
        affected_items=tuple(dict.fromkeys(
            " ".join(route.split())[:160] for route in routes
        ))[:200],
        outputs=outputs,
    )


def normalize_routing_neighbors(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if any(
            token in key
            for token in (
                "ospf_neighbor",
                "bgp_summary",
                "eigrp_neighbors",
                "rip_peers",
                "router_info_ospf_neighbor",
                "router_info_bgp_summary",
                "router_ospf_neighbors",
                "router_bgp_neighbors",
            )
        )
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No supported routing-protocol neighbor command returned data",
            reason_code="routing_protocol_not_configured_or_unsupported",
        )
    text = "\n".join(outputs.values())
    if raw.platform == "fortios" and re.search(
        r"(?im)^\s*%?\s*No neighbors exist\s*$",
        text,
    ):
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No OSPF or BGP neighbors are configured",
            reason_code="routing_protocol_neighbors_not_configured",
            metrics={"established_markers": 0, "problem_markers": 0},
            outputs=outputs,
        )
    established = len(re.findall(r"(?i)\bFULL(?:/|-)|\bESTABLISHED\b", text))
    if raw.platform == "fortios":
        established += sum(
            1
            for line in outputs.get("get_router_info_bgp_summary", "").splitlines()
            if re.match(
                r"^\s*(?:\d{1,3}\.){3}\d{1,3}\s+\d+\s+\d+.*\s+\d+\s*$",
                line,
            )
        )
    established += sum(
        1
        for line in outputs.get("show_ip_rip_peers", "").splitlines()
        if re.match(r"^\s*(?:\d{1,3}\.){3}\d{1,3}\b", line)
    )
    bad = len(re.findall(r"(?i)\b(?:DOWN|INIT|IDLE|ACTIVE)\b", text))
    observed = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if re.search(
            r"(?i)\b(?:FULL|ESTABLISHED|DOWN|INIT|IDLE|ACTIVE)\b",
            line,
        )
    )
    if bad:
        return _diagnostic(
            EvaluationOutcome.WARNING,
            f"Routing-neighbor output contains {bad} non-established states",
            reason_code="routing_neighbor_not_established",
            metrics={"established_markers": established, "problem_markers": bad},
            affected_items=observed[:100],
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Routing-neighbor inventory collected",
        reason_code="automatic_evaluation_not_configured",
        metrics={"established_markers": established, "problem_markers": 0},
        affected_items=observed[:100],
        outputs=outputs,
    )


def normalize_ha_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if any(
            token in key
            for token in (
                "show_switch", "stack", "standby", "redundancy", "system_ha_status",
                "system_ha_",
            )
        )
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No supported HA or stack command returned data",
            reason_code="ha_not_configured_or_unsupported",
        )
    text = "\n".join(outputs.values())
    structured_modes = tuple(
        value.casefold()
        for payload in _json_outputs(raw).values()
        for item in _walk_json(payload)
        for key, value in item.items()
        if "mode" in str(key).casefold() and isinstance(value, str)
    )
    if raw.platform == "fortios" and (
        re.search(r"(?im)^\s*Mode:\s*Standalone\s*$", text)
        or "standalone" in structured_modes
    ):
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "FortiGate is operating as a standalone unit",
            reason_code="ha_not_configured",
            metrics={"cluster_members": 0},
            outputs=outputs,
        )
    if raw.platform == "fortios":
        def field(pattern):
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            return match.group(1).strip() if match else None

        def integer(pattern):
            value = field(pattern)
            return int(value) if value is not None else None

        health_status = field(r"^\s*HA Health Status:\s*(.+?)\s*$")
        model = field(r"^\s*Model:\s*(.+?)\s*$")
        mode = field(r"^\s*Mode:\s*(.+?)\s*$")
        group_name = field(r"^\s*Group Name:\s*(.+?)\s*$")
        group_id = integer(r"^\s*Group ID:\s*(\d+)\s*$")
        declared_members = integer(r"^\s*number of member:\s*(\d+)\s*$")
        uptime_match = re.search(
            r"(?im)^\s*Cluster Uptime:\s*(\d+)\s+days\s+"
            r"(\d+)h:(\d+)m:(\d+)s\s*$",
            text,
        )
        cluster_uptime_seconds = None
        if uptime_match:
            days, hours, minutes, seconds = map(int, uptime_match.groups())
            cluster_uptime_seconds = (
                days * 86400 + hours * 3600 + minutes * 60 + seconds
            )

        members = {}

        def member(serial):
            return members.setdefault(serial, {
                "record_type": "ha_member",
                "hostname": None,
                "serial_number": serial,
                "role": "member",
                "cluster_index": None,
                "operating_index": None,
                "configuration_status": "unknown",
                "config_updated_seconds_ago": None,
                "sessions": None,
                "cpu_idle_percent": None,
                "memory_percent": None,
                "checksum": None,
                "heartbeat_interfaces": None,
                "heartbeat_all_up": None,
                "heartbeat_drop_total": None,
                "heartbeat_error_total": None,
            })

        for match in re.finditer(
            r"(?im)^\s*(\S+)\(updated\s+(\d+)\s+seconds?\s+ago\):\s*"
            r"(in-sync|out-of-sync)\s*$",
            text,
        ):
            serial, age, state = match.groups()
            item = member(serial)
            item["configuration_status"] = state.casefold()
            item["config_updated_seconds_ago"] = int(age)

        for match in re.finditer(
            r"(?im)^\s*(\S+)\s+chksum dump:\s*(.+?)\s*$",
            text,
        ):
            serial, checksum = match.groups()
            member(serial)["checksum"] = " ".join(checksum.split())

        for match in re.finditer(
            r"(?im)^\s*([^,\n]+?)\s*,\s*(\S+),\s*"
            r"HA cluster index\s*=\s*(\d+)\s*$",
            text,
        ):
            hostname, serial, index = match.groups()
            item = member(serial)
            item["hostname"] = hostname.strip()
            item["cluster_index"] = int(index)

        for match in re.finditer(
            r"(?im)^\s*(Primary|Secondary):\s*(\S+),\s*"
            r"HA operating index\s*=\s*(\d+)\s*$",
            text,
        ):
            role, serial, index = match.groups()
            item = member(serial)
            item["role"] = role.casefold()
            item["operating_index"] = int(index)

        for match in re.finditer(
            r"(?im)^\s*(\S+)\(updated\s+\d+\s+seconds?\s+ago\):\s*$"
            r"\n\s*sessions=(\d+),\s*"
            r"average-cpu-user/nice/system/idle="
            r"\d+%/\d+%/\d+%/(\d+)%,\s*memory=(\d+)%\s*$",
            text,
        ):
            serial, sessions, idle, memory = match.groups()
            item = member(serial)
            item["sessions"] = int(sessions)
            item["cpu_idle_percent"] = int(idle)
            item["memory_percent"] = int(memory)

        hb_section = re.search(
            r"(?ims)^HBDEV stats:\s*$\n(.*?)(?=^number of member:|\Z)",
            text,
        )
        heartbeat = {}
        if hb_section:
            current_serial = None
            for line in hb_section.group(1).splitlines():
                header = re.match(
                    r"^\s*(\S+)\(updated\s+\d+\s+seconds?\s+ago\):\s*$",
                    line,
                )
                if header:
                    current_serial = header.group(1)
                    member(current_serial)
                    heartbeat.setdefault(current_serial, [])
                    continue
                port = re.match(
                    r"^\s*(\S+):\s*[^,]+,\s*(up|down),\s*"
                    r"rx-bytes/packets/dropped/errors="
                    r"(\d+)/(\d+)/(\d+)/(\d+),\s*"
                    r"tx=(\d+)/(\d+)/(\d+)/(\d+)\s*$",
                    line,
                    re.IGNORECASE,
                )
                if port and current_serial:
                    (
                        name, state, _rx_bytes, _rx_packets, rx_dropped,
                        rx_errors, _tx_bytes, _tx_packets, tx_dropped,
                        tx_errors,
                    ) = port.groups()
                    heartbeat[current_serial].append({
                        "name": name,
                        "state": state.casefold(),
                        "drops": int(rx_dropped) + int(tx_dropped),
                        "errors": int(rx_errors) + int(tx_errors),
                    })

        heartbeat_down = 0
        heartbeat_drops = 0
        heartbeat_errors = 0
        for serial, interfaces in heartbeat.items():
            item = member(serial)
            item["heartbeat_interfaces"] = ",".join(
                interface["name"] for interface in interfaces
            ) or None
            item["heartbeat_all_up"] = bool(interfaces) and all(
                interface["state"] == "up" for interface in interfaces
            )
            item["heartbeat_drop_total"] = sum(
                interface["drops"] for interface in interfaces
            )
            item["heartbeat_error_total"] = sum(
                interface["errors"] for interface in interfaces
            )
            heartbeat_down += sum(
                interface["state"] != "up" for interface in interfaces
            )
            heartbeat_drops += item["heartbeat_drop_total"]
            heartbeat_errors += item["heartbeat_error_total"]

        checksums = [
            item["checksum"] for item in members.values() if item["checksum"]
        ]
        checksum_match = (
            len(set(checksums)) == 1 if len(checksums) >= 2 else None
        )
        in_sync = sum(
            item["configuration_status"] == "in-sync"
            for item in members.values()
        )
        out_of_sync = sum(
            item["configuration_status"] == "out-of-sync"
            for item in members.values()
        )
        parsed_members = len(members)
        primary = next((
            serial for serial, item in members.items()
            if item["role"] == "primary"
        ), None)
        secondary = next((
            serial for serial, item in members.items()
            if item["role"] == "secondary"
        ), None)

        metrics = {
            "ha_configured": bool(mode),
            "health_status": health_status,
            "model": model,
            "mode": mode,
            "group_name": group_name,
            "group_id": group_id,
            "cluster_uptime_seconds": cluster_uptime_seconds,
            "declared_member_count": declared_members,
            "parsed_member_count": parsed_members,
            "in_sync_member_count": in_sync,
            "out_of_sync_member_count": out_of_sync,
            "checksum_match": checksum_match,
            "primary_serial": primary,
            "secondary_serial": secondary,
            "heartbeat_down_count": heartbeat_down,
            "heartbeat_drop_total": heartbeat_drops,
            "heartbeat_error_total": heartbeat_errors,
        }
        def heartbeat_state(value):
            if value is True:
                return "up"
            if value is False:
                return "down"
            return "unverified"

        member_evidence = tuple(
            (
                f"{item['hostname'] or 'unknown'} ({serial}): "
                f"role={item['role']}, config={item['configuration_status']}, "
                f"heartbeat={heartbeat_state(item['heartbeat_all_up'])}"
            )[:240]
            for serial, item in members.items()
        )

        if health_status and health_status.casefold() != "ok":
            outcome = EvaluationOutcome.FAIL
            reason = "ha_health_not_ok"
            summary = f"FortiOS HA health status is {health_status}"
        elif out_of_sync or checksum_match is False:
            outcome = EvaluationOutcome.FAIL
            reason = "ha_configuration_out_of_sync"
            summary = (
                f"FortiOS HA configuration is not synchronized: "
                f"{out_of_sync} out-of-sync member(s)"
            )
        elif heartbeat_down:
            outcome = EvaluationOutcome.WARNING
            reason = "ha_heartbeat_interface_down"
            summary = (
                f"FortiOS HA has {heartbeat_down} down heartbeat interface(s)"
            )
        elif (
            not health_status
            or declared_members is None
            or parsed_members != declared_members
            or not members
            or (
                mode is not None
                and "a-p" in mode.casefold()
                and (not primary or not secondary)
            )
        ):
            outcome = EvaluationOutcome.UNKNOWN
            reason = "ha_inventory_incomplete"
            summary = "FortiOS HA evidence is incomplete"
        elif in_sync != declared_members:
            outcome = EvaluationOutcome.UNKNOWN
            reason = "ha_sync_state_incomplete"
            summary = (
                f"FortiOS HA returned sync state for {in_sync} of "
                f"{declared_members} members"
            )
        else:
            outcome = EvaluationOutcome.PASS
            reason = None
            summary = (
                f"FortiOS HA cluster {group_name or '-'} is healthy; "
                f"{in_sync}/{declared_members} members are in sync"
            )
        return _diagnostic(
            outcome,
            summary,
            reason_code=reason,
            metrics=metrics,
            affected_items=member_evidence,
            records=tuple(members.values()),
            outputs=outputs,
        )
    bad = re.findall(r"(?im)^.*\b(?:failed|down|not ready|mismatch)\b.*$", text)
    if bad:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            f"HA/stack output contains {len(bad)} fault states",
            reason_code="ha_fault_detected",
            metrics={"fault_count": len(bad)},
            affected_items=(" ".join(line.split())[:160] for line in bad[:20]),
            outputs=outputs,
        )
    healthy = re.findall(
        r"(?i)\b(?:active|standby|ready|member|master|primary|secondary|in-sync)\b",
        text,
    )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "HA/stack inventory collected",
        reason_code="automatic_evaluation_not_configured",
        metrics={"healthy_markers": len(healthy)},
        outputs=outputs,
    )


def normalize_session_health(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("session", "performance", "resource_usage"))
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No session or resource-usage evidence was returned",
            reason_code="session_health_unsupported",
        )
    text = "\n".join(outputs.values())
    conserve = bool(re.search(r"(?i)conserve(?:\s+mode)?.{0,30}(?:on|active|entered)", text))
    sessions = None
    match = re.search(r"(?i)(?:session count|sessions?)\s*[:=]\s*(\d+)", text)
    if match:
        sessions = int(match.group(1))
    if conserve:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "FortiOS reports conserve mode",
            reason_code="conserve_mode_active",
            metrics={"conserve_mode": True, "session_count": sessions},
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Session evidence collected; no capacity policy or history is configured",
        reason_code="session_policy_or_history_unavailable",
        metrics={"conserve_mode": False, "session_count": sessions},
        outputs=outputs,
    )


def normalize_stability_events(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("crash", "config_error", "reboot"))
    }
    if not outputs:
        command_outputs = _command_outputs(raw)
        if any("diagnose" in key for key in command_outputs):
            return _diagnostic(
                EvaluationOutcome.UNKNOWN,
                "Stability diagnostics require additional FortiOS diagnose permission",
                reason_code="fortios_diagnose_access_disabled",
            )
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported stability evidence was returned",
            reason_code="stability_evidence_unsupported",
        )
    findings = tuple(
        " ".join(line.split())[:160]
        for value in outputs.values()
        for line in value.splitlines()
        if re.search(r"(?i)\b(?:crash|segfault|panic|unexpected reboot|error)\b", line)
        and not re.search(r"(?i)\b(?:no|zero|0)\s+(?:crash|error)", line)
    )
    if findings:
        return _diagnostic(
            EvaluationOutcome.WARNING,
            "Recent stability evidence requires review",
            reason_code="stability_events_detected",
            metrics={"finding_count": len(findings), "history_comparison": "unavailable"},
            affected_items=findings[:20],
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "No stability event was parsed from the current sample; historical comparison is unavailable",
        reason_code="stability_history_unavailable",
        metrics={"finding_count": 0, "history_comparison": "unavailable"},
        outputs=outputs,
    )


def normalize_storage_health(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("disk", "sensor", "monitor_sensor"))
    }
    if not outputs:
        if _fortios_is_vm(raw):
            return _diagnostic(
                EvaluationOutcome.NOT_APPLICABLE,
                "No physical storage sensor applies to this FortiGate VM",
                reason_code="virtual_appliance_storage_sensor_not_applicable",
            )
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported storage-health evidence was returned",
            reason_code="storage_health_unsupported",
        )
    text = "\n".join(outputs.values())
    faults = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if re.search(r"(?i)\b(?:disk|storage|ssd|flash)\b", line)
        and re.search(r"(?i)\b(?:fail|fault|critical|degraded|read-only)\b", line)
    )
    if faults:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "Storage evidence contains an explicit fault state",
            reason_code="storage_fault_detected",
            metrics={"fault_count": len(faults)},
            affected_items=faults[:20],
            outputs=outputs,
        )
    if raw.platform == "fortios":
        unit_to_gib = {
            "KiB": 1 / (1024 * 1024),
            "MiB": 1 / 1024,
            "GiB": 1,
            "TiB": 1024,
        }
        disks = re.findall(
            r"(?im)^Disk\s+.+?\s+(?:ref:\s*\d+\s+)?"
            r"(\d+(?:\.\d+)?)(KiB|MiB|GiB|TiB)\s+type:",
            text,
        )
        partitions = re.findall(
            r"(?im)^\s*partition(?:\s+ref:\s*\d+)?\s+"
            r"(\d+(?:\.\d+)?)(KiB|MiB|GiB|TiB),\s+"
            r"(\d+(?:\.\d+)?)(KiB|MiB|GiB|TiB)\s+free\b",
            text,
        )
        available_match = re.search(
            r"(?im)^Total available disks:\s*(\d+)\s*$",
            text,
        )
        storage_match = re.search(
            r"(?im)\bAvailable storage disks:\s*(\d+)\b",
            text,
        )
        if disks or partitions or available_match or storage_match:
            total_capacity = sum(
                float(value) * unit_to_gib[unit] for value, unit in disks
            )
            partition_capacity = sum(
                float(total) * unit_to_gib[total_unit]
                for total, total_unit, _free, _free_unit in partitions
            )
            partition_free = sum(
                float(free) * unit_to_gib[free_unit]
                for _total, _total_unit, free, free_unit in partitions
            )
            metrics = {
                "disk_count": len(disks),
                "total_capacity_gib": round(total_capacity, 3),
                "partition_count": len(partitions),
                "partition_capacity_gib": round(partition_capacity, 3),
                "partition_free_gib": round(partition_free, 3),
            }
            if available_match:
                metrics["available_disk_count"] = int(
                    available_match.group(1)
                )
            if storage_match:
                metrics["available_storage_disk_count"] = int(
                    storage_match.group(1)
                )
            return _diagnostic(
                EvaluationOutcome.UNKNOWN,
                "FortiOS storage inventory collected without a health threshold",
                reason_code="storage_threshold_unconfigured",
                metrics=metrics,
                outputs=outputs,
            )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Storage evidence was collected but model-specific health semantics are not verified",
        reason_code="storage_health_format_unverified",
        outputs=outputs,
    )


def normalize_fortiguard_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("autoupdate", "fortiguard", "license_status"))
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No FortiGuard or update status evidence was returned",
            reason_code="fortiguard_status_unsupported",
        )
    text = "\n".join(outputs.values())
    expired = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if re.search(r"(?i)\b(?:expired|invalid|unlicensed)\b", line)
    )
    unreachable = bool(re.search(r"(?i)\b(?:unreachable|connection failed|timeout)\b", text))
    if expired:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "FortiGuard evidence contains an expired or invalid entitlement",
            reason_code="fortiguard_entitlement_invalid",
            metrics={"entitlement_problem_count": len(expired), "service_reachable": not unreachable},
            affected_items=expired[:20],
            outputs=outputs,
        )
    if unreachable:
        return _diagnostic(
            EvaluationOutcome.WARNING,
            "FortiGuard service reachability problem was reported",
            reason_code="fortiguard_service_unreachable",
            metrics={"service_reachable": False},
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "FortiGuard data was collected; entitlement, database currency and reachability require separate verified rules",
        reason_code="fortiguard_policy_unconfigured",
        metrics={"service_reachable": None, "database_currency": "unverified"},
        outputs=outputs,
    )


def normalize_acme_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if "acme" in key
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No ACME certificate status was returned",
            reason_code="acme_not_configured_or_unsupported",
        )
    text = "\n".join(outputs.values())
    failures = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if re.search(r"(?i)\b(?:failed|error|invalid|expired)\b", line)
    )
    if failures:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "ACME evidence contains a renewal or certificate failure",
            reason_code="acme_failure_detected",
            metrics={"failure_count": len(failures)},
            affected_items=failures[:20],
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "ACME status was collected; successful automatic renewal is not proven by configuration alone",
        reason_code="acme_renewal_not_verified",
        outputs=outputs,
    )


def _discovered_state_check(
    raw: RawCollectorResult,
    *,
    output_tokens: tuple[str, ...],
    unsupported_code: str,
    label: str,
) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in output_tokens)
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            f"No supported {label} evidence was returned",
            reason_code=unsupported_code,
        )
    text = "\n".join(outputs.values())
    reason_prefix = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    if re.search(
        r"(?i)\b(?:not configured|no entries|no tunnels?|no members?)\b",
        text,
    ):
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            f"No configured {label} entries were reported",
            reason_code=f"{reason_prefix}_not_configured",
            outputs=outputs,
        )
    unhealthy = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if re.search(r"(?i)\b(?:down|dead|failed|error|inactive)\b", line)
    )
    if unhealthy:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN,
            f"{label.capitalize()} inventory contains non-active states",
            reason_code="automatic_evaluation_not_configured",
            metrics={"non_active_markers": len(unhealthy)},
            affected_items=unhealthy[:20],
            outputs=outputs,
        )
    active = len(re.findall(
        r"(?i)\b(?:up|alive|established|success|healthy)\b", text
    ))
    observed = tuple(
        " ".join(line.split())[:160]
        for line in text.splitlines()
        if line.strip()
    )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        f"{label.capitalize()} inventory collected",
        reason_code="automatic_evaluation_not_configured",
        metrics={"active_markers": active},
        affected_items=observed[:100],
        outputs=outputs,
    )


def normalize_sdwan_health(raw: RawCollectorResult) -> DiagnosticValues:
    base = _discovered_state_check(
        raw,
        output_tokens=("sdwan", "virtual_wan"),
        unsupported_code="sdwan_health_unsupported",
        label="SD-WAN health check",
    )
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("sdwan", "virtual_wan"))
    }
    text = "\n".join(outputs.values())
    measurements = {
        "packet_loss_percent": [
            float(value) for value in re.findall(
                r"(?i)(?:packet[-_ ]?loss|loss)\D{0,20}(\d+(?:\.\d+)?)\s*%",
                text,
            )
        ],
        "latency_ms": [
            float(value) for value in re.findall(
                r"(?i)latency\D{0,20}(\d+(?:\.\d+)?)\s*(?:ms)?",
                text,
            )
        ],
        "jitter_ms": [
            float(value) for value in re.findall(
                r"(?i)jitter\D{0,20}(\d+(?:\.\d+)?)\s*(?:ms)?",
                text,
            )
        ],
    }
    metrics = dict(base.metrics)
    metrics.update({
        "maximum_packet_loss_percent": max(
            measurements["packet_loss_percent"], default=None
        ),
        "maximum_latency_ms": max(measurements["latency_ms"], default=None),
        "maximum_jitter_ms": max(measurements["jitter_ms"], default=None),
    })
    return base.model_copy(update={"metrics": metrics})


def normalize_vpn_tunnels(raw: RawCollectorResult) -> DiagnosticValues:
    return _discovered_state_check(
        raw,
        output_tokens=("vpn", "ipsec", "tunnel"),
        unsupported_code="vpn_tunnel_status_unsupported",
        label="VPN tunnel",
    )


def normalize_log_delivery(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("fortianalyzer", "syslog", "log_queue"))
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNKNOWN,
            "No receiver-side evidence of log delivery is available",
            reason_code="log_delivery_unverified",
        )
    text = "\n".join(outputs.values())
    if re.search(r"(?i)\b(?:queue full|connection failed|unreachable|disconnected)\b", text):
        return _diagnostic(
            EvaluationOutcome.WARNING,
            "Log forwarding evidence reports a delivery problem",
            reason_code="log_delivery_problem_detected",
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Forwarding configuration or queue state was collected; receiver-side delivery remains unverified",
        reason_code="log_receiver_confirmation_unavailable",
        outputs=outputs,
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


def _first_mapping_text(mapping: dict, *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            first = next(
                (
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                ),
                None,
            )
            if first is not None:
                return first
    return None


def _parse_cisco_ntc_device_facts(
    raw: RawCollectorResult,
    *,
    ntc_platform: str,
) -> DeviceFactsValues:
    data = raw.data
    if not isinstance(data, dict):
        raise ParseFailedError()
    version_output = data.get("show_version")
    inventory_output = data.get("show_inventory")
    if (
        not isinstance(version_output, str)
        or not version_output.strip()
        or not isinstance(inventory_output, str)
        or not inventory_output.strip()
    ):
        raise ParseFailedError()
    try:
        from ntc_templates.parse import parse_output

        version_rows = parse_output(
            platform=ntc_platform,
            command="show version",
            data=version_output,
        )
        inventory_rows = parse_output(
            platform=ntc_platform,
            command="show inventory",
            data=inventory_output,
        )
    except Exception:
        raise ParseFailedError() from None
    if not version_rows or not isinstance(version_rows[0], dict):
        raise ParseFailedError()
    version = version_rows[0]
    model = _first_text(inventory_rows, "pid") or _first_mapping_text(
        version,
        "model",
        "platform",
        "hardware",
    )
    serial_number = _first_text(inventory_rows, "sn") or _first_mapping_text(
        version,
        "serial",
    )
    uptime = _first_mapping_text(version, "uptime")
    if uptime is None:
        raise ParseFailedError()
    return _device_facts({
        "hostname": _first_mapping_text(version, "hostname"),
        "model": model,
        "os_version": _first_mapping_text(version, "os", "version"),
        "serial_number": serial_number,
        "uptime_seconds": _text_uptime_seconds(uptime),
    })


def parse_cisco_nxos_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    return _parse_cisco_ntc_device_facts(raw, ntc_platform="cisco_nxos")


def parse_cisco_asa_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    return _parse_cisco_ntc_device_facts(raw, ntc_platform="cisco_asa")


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
    if data.get("source_transport") == "ssh":
        status = data.get("show_system_status")
        performance = data.get("show_performance_status")
        if not isinstance(status, str) or not isinstance(performance, str):
            raise ParseFailedError()

        def field(label: str, text: str) -> str | None:
            match = re.search(
                rf"(?im)^\s*{re.escape(label)}\s*:\s*(?P<value>\S.*?)\s*$",
                text,
            )
            return match.group("value").strip() if match else None

        version_line = field("Version", status)
        version_match = (
            re.match(
                r"(?P<model>\S+)\s+v(?P<version>[^,\s]+)",
                version_line,
                re.IGNORECASE,
            )
            if version_line
            else None
        )
        uptime = field("Uptime", performance)
        if version_match is None or uptime is None:
            raise ParseFailedError()
        return _device_facts(
            {
                "hostname": field("Hostname", status),
                "model": version_match.group("model"),
                "os_version": version_match.group("version"),
                "serial_number": field("Serial-Number", status),
                "uptime_seconds": _text_uptime_seconds(uptime),
            }
        )
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


def _gaia_field(text: str, labels: tuple[str, ...]) -> str | None:
    alternatives = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?im)^\s*(?:{alternatives})\s*:\s*(?P<value>\S.*?)\s*$",
        text,
    )
    return match.group("value").strip() if match else None


def _gaia_model(text: str) -> str | None:
    model = _gaia_field(
        text,
        ("Platform", "Appliance Model", "Model", "Product Name"),
    )
    if model is None:
        return None
    security_gateway = re.fullmatch(r"(?i)Check\s+Point\s+(\d+)", model)
    if security_gateway:
        return "SG" + security_gateway.group(1)
    return model


def _text_uptime_seconds(text: str) -> int:
    value = " ".join(text.split()).replace("(s)", "s")
    total = 0
    found = False
    for amount, unit in re.findall(
        r"(?i)(\d+)\s*(years?|weeks?|days?|hours?|minutes?|seconds?)",
        value,
    ):
        name = unit.lower().rstrip("s") + "s"
        total += _duration_seconds(**{name: int(amount)})
        found = True
    clock = re.search(
        r"(?<!\d)(?P<hours>\d{1,3}):(?P<minutes>\d{2})(?::(?P<seconds>\d{2}))?(?!\d)",
        value,
    )
    if clock:
        total += _duration_seconds(**{
            key: int(amount or 0) for key, amount in clock.groupdict().items()
        })
        found = True
    if not found:
        raise ParseFailedError()
    return total


def _gaia_uptime_seconds(text: str) -> int:
    normalized = " ".join(text.split())
    labelled = re.search(r"(?i)\buptime\s*:\s*(?P<value>.+)$", normalized)
    linux_uptime = re.search(
        r"(?i)\bup\s+(?P<value>.+?)"
        r"(?:,\s*\d+\s+users?\b|,\s*load\s+average\b|$)",
        normalized,
    )
    value = (
        labelled.group("value")
        if labelled
        else linux_uptime.group("value")
        if linux_uptime
        else normalized
    )
    return _text_uptime_seconds(value)


def parse_checkpoint_gaia_device_facts(raw: RawCollectorResult) -> DeviceFactsValues:
    data = raw.data
    if not isinstance(data, dict):
        raise ParseFailedError()
    outputs = {}
    for key in ("show_hostname", "show_asset_all", "show_version_all", "show_uptime"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ParseFailedError()
        outputs[key] = value

    hostname = _gaia_field(outputs["show_hostname"], ("Hostname",))
    if hostname is None:
        hostname_lines = [
            line.strip()
            for line in outputs["show_hostname"].splitlines()
            if line.strip()
        ]
        if len(hostname_lines) == 1 and " " not in hostname_lines[0]:
            hostname = hostname_lines[0]
    model = _gaia_model(outputs["show_asset_all"])
    serial_number = _gaia_field(
        outputs["show_asset_all"],
        ("Serial Number", "Serial"),
    )
    os_version = _gaia_field(
        outputs["show_version_all"],
        ("Product version", "Gaia version", "Version"),
    )
    if os_version is None:
        match = re.search(r"(?i)\b(R\d+(?:\.\d+)+)\b", outputs["show_version_all"])
        os_version = match.group(1) if match else None
    return _device_facts({
        "hostname": hostname,
        "model": model,
        "os_version": os_version,
        "serial_number": serial_number,
        "uptime_seconds": _gaia_uptime_seconds(outputs["show_uptime"]),
    })


DEVICE_FACTS_PARSERS = {
    "cisco-ios": parse_cisco_ios_device_facts,
    "cisco-ios-xe": parse_cisco_ios_device_facts,
    "cisco-cbs": parse_cisco_cbs_device_facts,
    "cisco-cbs250-350": parse_cisco_cbs_device_facts,
    "cisco-nxos": parse_cisco_nxos_device_facts,
    "cisco-asa": parse_cisco_asa_device_facts,
    "fortios": parse_fortios_device_facts,
    "aruba-aos-cx": parse_aruba_aoscx_device_facts,
    "checkpoint-gaia": parse_checkpoint_gaia_device_facts,
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
    "normalize_command_cpu",
    "normalize_cpu",
    "normalize_device_facts",
    "normalize_environment_health",
    "normalize_acme_status",
    "normalize_fortiguard_status",
    "normalize_fan_health",
    "normalize_ha_status",
    "normalize_interface_errors",
    "normalize_interface_state",
    "normalize_interface_utilization",
    "normalize_license_state",
    "normalize_memory",
    "normalize_ntp_status",
    "normalize_os_version",
    "normalize_pki_certificates",
    "normalize_recent_logs",
    "normalize_log_delivery",
    "normalize_routing_neighbors",
    "normalize_routing_table",
    "normalize_sdwan_health",
    "normalize_session_health",
    "normalize_stability_events",
    "normalize_storage_health",
    "normalize_temperature",
    "normalize_uptime",
    "normalize_vpn_tunnels",
    "parse_aruba_aoscx_device_facts",
    "parse_cisco_cbs_device_facts",
    "parse_cisco_cbs_cpu",
    "parse_cisco_ios_device_facts",
    "parse_checkpoint_gaia_device_facts",
    "parse_cisco_ios_cpu",
    "parse_fortios_cpu",
    "parse_fortios_device_facts",
]
