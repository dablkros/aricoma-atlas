"""Platform-specific parsing kept separate from Ansible transport."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    outputs=None,
) -> DiagnosticValues:
    return DiagnosticValues(
        outcome=outcome,
        reason_code=reason_code,
        summary=summary,
        metrics=metrics or {},
        affected_items=tuple(affected_items),
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
            for token in ("environment", "power_inline", "post", "system_resources")
        )
    }


def normalize_environment_health(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _environment_outputs(raw)
    if not outputs:
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
    if values:
        return SystemMetricValues(
            outcome=EvaluationOutcome.UNKNOWN,
            value=max(values),
            unit="celsius",
            reason_code="temperature_threshold_unconfigured",
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
        if any(token in key for token in ("interface", "interfaces"))
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
    for value in outputs.values():
        for line in value.splitlines():
            normalized = " ".join(line.split())
            if re.search(r"\berr-disabled\b", normalized, re.I):
                bad.append(normalized[:160])
            elif re.search(
                r"\b(?:administratively down|notconnect|down/down)\b",
                normalized,
                re.I,
            ):
                down.append(normalized[:160])
    if bad:
        return _diagnostic(
            EvaluationOutcome.FAIL,
            f"{len(bad)} interfaces are err-disabled",
            reason_code="interface_err_disabled",
            metrics={"err_disabled": len(bad), "down_or_unused": len(down)},
            affected_items=bad,
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Interface state collected; expected administrative state is not configured",
        reason_code="interface_expectation_unconfigured",
        metrics={"err_disabled": 0, "down_or_unused": len(down)},
        affected_items=down[:20],
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
        if "license" in key
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
                r"(?im)^\s*(?:end date|not after|valid until)\s*:\s*"
                r"(?P<date>.+?)\s*$",
                value,
            )
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


def _installed_version(outputs: dict[str, str]) -> str | None:
    text = outputs.get("show_version", "")
    for pattern in (
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
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Installed firmware collected; no reviewed recommendation catalog is configured",
        reason_code="firmware_catalog_unavailable",
        metrics={"installed_version": version},
        outputs={key: value for key, value in outputs.items() if key == "show_version"},
    )


def normalize_eol_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = _usable_outputs(raw)
    inventory = outputs.get("show_inventory", "")
    product_ids = tuple(dict.fromkeys(re.findall(r"(?im)\bPID:\s*(\S+)", inventory)))
    return _diagnostic(
        EvaluationOutcome.UNKNOWN,
        "Product identifiers collected; no reviewed lifecycle record is configured",
        reason_code="lifecycle_catalog_unavailable",
        metrics={
            "product_id_count": len(product_ids),
            "primary_product_id": product_ids[0] if product_ids else None,
        },
        outputs={key: value for key, value in outputs.items() if key == "show_inventory"},
    )


def normalize_ntp_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if "ntp" in key or "sntp" in key or key == "show_clock"
    }
    text = "\n".join(outputs.values())
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.UNSUPPORTED,
            "No supported NTP/SNTP command returned data",
            reason_code="ntp_command_unsupported",
        )
    if re.search(r"(?i)\b(?:unsynchronized|not synchronized|not synced)\b", text):
        return _diagnostic(
            EvaluationOutcome.FAIL,
            "Device clock is not synchronized",
            reason_code="ntp_unsynchronized",
            outputs=outputs,
        )
    if re.search(r"(?i)\b(?:clock is synchronized|synchronized, stratum|status:\s*up)\b", text):
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
        if "logging" in key or "logfile" in key or "crash" in key
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
        if key in {"show_ip_route", "show_route"}
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
        EvaluationOutcome.PASS if routes or default else EvaluationOutcome.UNKNOWN,
        "Routing table collected" if routes or default else "Routing table format is unrecognized",
        reason_code=None if routes or default else "routing_table_unrecognized",
        metrics={"parsed_routes": len(routes), "default_route_present": default},
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
    established = len(re.findall(r"(?i)\bFULL(?:/|-)|\bESTABLISHED\b", text))
    established += sum(
        1
        for line in outputs.get("show_ip_rip_peers", "").splitlines()
        if re.match(r"^\s*(?:\d{1,3}\.){3}\d{1,3}\b", line)
    )
    bad = len(re.findall(r"(?i)\b(?:DOWN|INIT|IDLE|ACTIVE)\b", text))
    if bad:
        return _diagnostic(
            EvaluationOutcome.WARNING,
            f"Routing-neighbor output contains {bad} non-established states",
            reason_code="routing_neighbor_not_established",
            metrics={"established_markers": established, "problem_markers": bad},
            outputs=outputs,
        )
    return _diagnostic(
        EvaluationOutcome.PASS if established else EvaluationOutcome.UNKNOWN,
        "Routing-neighbor output collected",
        reason_code=None if established else "routing_neighbor_format_unrecognized",
        metrics={"established_markers": established, "problem_markers": 0},
        outputs=outputs,
    )


def normalize_ha_status(raw: RawCollectorResult) -> DiagnosticValues:
    outputs = {
        key: value
        for key, value in _usable_outputs(raw).items()
        if any(token in key for token in ("show_switch", "stack", "standby", "redundancy"))
    }
    if not outputs:
        return _diagnostic(
            EvaluationOutcome.NOT_APPLICABLE,
            "No supported HA or stack command returned data",
            reason_code="ha_not_configured_or_unsupported",
        )
    text = "\n".join(outputs.values())
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
    healthy = re.findall(r"(?i)\b(?:active|standby|ready|member|master)\b", text)
    return _diagnostic(
        EvaluationOutcome.PASS if healthy else EvaluationOutcome.UNKNOWN,
        "HA/stack output collected",
        reason_code=None if healthy else "ha_state_unrecognized",
        metrics={"healthy_markers": len(healthy)},
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
    "normalize_eol_status",
    "normalize_fan_health",
    "normalize_firmware_recommendation",
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
    "normalize_routing_neighbors",
    "normalize_routing_table",
    "normalize_temperature",
    "normalize_uptime",
    "parse_aruba_aoscx_device_facts",
    "parse_cisco_cbs_device_facts",
    "parse_cisco_cbs_cpu",
    "parse_cisco_ios_device_facts",
    "parse_checkpoint_gaia_device_facts",
    "parse_cisco_ios_cpu",
    "parse_fortios_cpu",
    "parse_fortios_device_facts",
]
