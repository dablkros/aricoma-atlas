"""Versioned parser-tolerant Cisco CLI collection contract."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.resources import files
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.prophylaxis.cisco_registry import (
    DEFAULT_CISCO_COMMAND_REGISTRY,
    ResolvedCiscoCommand,
)


RAW_SCHEMA_VERSION = "1.0"
CISCO_RAW_SCHEMA_VERSION = RAW_SCHEMA_VERSION
CISCO_COMMAND_REGISTRY = DEFAULT_CISCO_COMMAND_REGISTRY
CISCO_RAW_PLATFORMS = CISCO_COMMAND_REGISTRY.platforms
# Backwards-compatible derived views. The platform matrix itself lives only in YAML.
CISCO_IDENTITY_COMMANDS = CISCO_COMMAND_REGISTRY.all_commands("identity")
CISCO_FULL_COMMANDS = CISCO_COMMAND_REGISTRY.all_commands("full_collection")


def cisco_raw_commands(platform: str, mode: str) -> tuple[str, ...]:
    """Resolve the ordered, platform-specific command set from the registry."""
    return tuple(
        item.command for item in CISCO_COMMAND_REGISTRY.resolve(platform, mode)
    )

CommandStatus = Literal[
    "success", "unsupported", "empty", "permission_denied", "timeout",
    "error", "truncated",
]
_UNSUPPORTED = re.compile(
    r"(?i)(invalid input|invalid command|unrecognized command|unknown command|"
    r"incomplete command|ambiguous command|not supported)"
)
_PERMISSION = re.compile(
    r"(?i)(permission denied|authorization failed|not authorized|insufficient "
    r"privilege|privilege level)"
)
_TIMEOUT = re.compile(r"(?i)(timed? out|command timeout|timeout waiting)")
_FORTIOS_UNSUPPORTED = re.compile(
    r"(?i)(command parse error before|invalid command|unknown command|"
    r"not a valid command|unrecognized parameter|syntax error)"
)
_FORTIOS_PERMISSION = re.compile(
    r"(?i)(permission denied|access denied|not authorized|insufficient privilege|"
    r"requires [a-z0-9_-]+ privilege)"
)
_PROMPT_ONLY = re.compile(r"(?m)^\s*[A-Za-z0-9_.()/-]+[>#]\s*$")
_SECRET_LINES = (
    re.compile(r"(?i)^(\s*(?:proxy\s+)?password\s*:\s*).*$"),
    re.compile(
        r"(?i)^(\s*username\s+\S+.*?\b(?:password|secret)\b(?:\s+\d+)?\s+).*$"
    ),
    re.compile(r"(?i)^(\s*(?:username\s+\S+\s+)?(?:password|secret)\s+).*$"),
    re.compile(r"(?i)^(\s*enable\s+(?:password|secret)\s+).*$"),
    re.compile(r"(?i)^(\s*snmp-server\s+community\s+).*$"),
    re.compile(r"(?i)^(\s*snmp-server\s+user\s+).*$"),
    re.compile(r"(?i)^(\s*(?:radius-server|tacacs-server).*\bkey\s+).*$"),
    re.compile(r"(?i)^(\s*crypto\s+isakmp\s+key\s+).*$"),
    re.compile(r"(?i)^(\s*neighbor\s+\S+\s+password(?:\s+\d+)?\s+).*$"),
    re.compile(r"(?i)^(\s*(?:authentication-key|shared-secret)\s+).*$"),
    re.compile(r"(?i)^(\s*key\s+\d+\s+).*$"),
    re.compile(r"(?i)^(\s*(?:pre-shared-key|key-string)\s+).*$"),
    re.compile(r"(?i)^(\s*encrypted\s+cbd\s+key\b.*?\bsecret\s+).*$"),
    re.compile(
        r"(?i)^(\s*set\s+(?:password|passwd|secret|psksecret|private-key|"
        r"community)\s+).*$"
    ),
)

_SMB_CONFIG_VERSION = re.compile(r"(?m)^v(?P<version>\d+(?:\.\d+)+)\s+/\s+")
_SMB_HOSTNAME = re.compile(r"(?m)^hostname\s+(?P<hostname>\S+)\s*$")
_SMB_VLAN_DATABASE = re.compile(r"(?m)^vlan\s+(?P<definition>[0-9,-]+)\s*$")
_SMB_VLAN_INTERFACE = re.compile(r"(?m)^interface\s+vlan\s+(?P<vlan_id>\d+)\s*$")
_SMB_USERNAME = re.compile(r"(?m)^username\s+\S+\s+")


def _configured(text: str, statement: str) -> bool | None:
    """Return true/false for an explicit statement, otherwise unknown."""
    state: bool | None = None
    positive = re.compile(rf"^{re.escape(statement)}(?:\s|$)")
    negative = re.compile(rf"^no\s+{re.escape(statement)}(?:\s|$)")
    for line in text.splitlines():
        candidate = line.strip()
        if positive.match(candidate):
            state = True
        elif negative.match(candidate):
            state = False
    return state


def _parse_cisco_smb_running_config(text: str) -> dict[str, Any] | None:
    """Extract a small, non-secret summary from reviewed Cisco SMB syntax."""
    hostname = _SMB_HOSTNAME.search(text)
    version = _SMB_CONFIG_VERSION.search(text)
    vlan_definitions = _SMB_VLAN_DATABASE.findall(text)
    vlan_interfaces = tuple(dict.fromkeys(_SMB_VLAN_INTERFACE.findall(text)))
    if not any((hostname, version, vlan_definitions, vlan_interfaces)):
        return None
    return {
        "hostname": hostname.group("hostname") if hostname else None,
        "config_version": version.group("version") if version else None,
        "ip_routing_enabled": _configured(text, "ip routing"),
        "ssh_server_enabled": _configured(text, "ip ssh server"),
        "snmp_agent_enabled": _configured(text, "snmp-server server"),
        "sntp_client_enabled": _configured(text, "sntp unicast client enable"),
        "configured_user_count": len(_SMB_USERNAME.findall(text)),
        "vlan_definitions": vlan_definitions,
        "vlan_interface_ids": list(vlan_interfaces),
    }


_PRIVATE_KEY_BLOCK = re.compile(
    r"(?ms)-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)


class CiscoRawCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: str = Field(min_length=1, max_length=256)
    status: CommandStatus
    duration_ms: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    stdout: str | None = None
    reason_code: str | None = Field(default=None, max_length=128)
    truncated: bool = False
    parser_status: Literal["not_requested", "parsed", "template_missing", "failed"] = "not_requested"
    parsing_status: Literal[
        "success", "no_parser", "empty_result", "parse_error", "skipped"
    ] = "skipped"
    command_id: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    parser_engine: Literal["ntc_textfsm", "atlas_custom", "none"] | None = None
    parser_platform: str | None = Field(default=None, max_length=64)
    parser_template: str | None = Field(default=None, max_length=255)
    parser_version: str | None = Field(default=None, max_length=32)
    structured_data: list[dict[str, Any]] | None = None
    normalized_data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status in {"success", "truncated"}:
            if not self.stdout or self.reason_code is not None:
                raise ValueError("usable command output requires stdout only")
        elif self.stdout is not None or not self.reason_code:
            raise ValueError("ignored command requires only a safe reason code")
        if self.status == "truncated" and not self.truncated:
            raise ValueError("truncated status requires truncated=true")
        if self.parser_status == "parsed" and self.structured_data is None:
            raise ValueError("parsed output requires structured data")
        if self.parsing_status == "success" and self.parser_status != "parsed":
            raise ValueError("successful parsing requires legacy parsed status")
        if self.parsing_status == "success" and self.structured_data is None:
            raise ValueError("successful parsing requires structured data")
        return self


class CiscoRawIgnoredCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: str = Field(min_length=1, max_length=256)
    status: Literal["unsupported", "empty", "permission_denied", "timeout", "error"]
    reason_code: str = Field(min_length=1, max_length=128)
    duration_ms: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime


class CiscoRawScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)


class CiscoRawDeviceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    netbox_id: int = Field(ge=1)
    hostname: str = Field(min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=128)
    manufacturer: str | None = Field(default=None, max_length=255)
    device_type: str | None = Field(default=None, max_length=255)
    management_ip: str = Field(min_length=1, max_length=255)
    tenant: CiscoRawScope
    site: CiscoRawScope
    credential_profile: str | None = Field(default=None, max_length=255)


class CiscoRawConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transport: Literal["ssh", "telnet"]
    status: Literal["connected", "failed"]
    port: int | None = Field(default=None, ge=1, le=65535)
    reason_code: str | None = Field(default=None, max_length=128)


class CiscoRawCollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["completed", "completed_with_errors", "failed"]
    total_commands: int = Field(ge=1, le=128)
    attempted: int = Field(default=0, ge=0, le=128)
    successful: int = Field(default=0, ge=0, le=128)
    unsupported: int = Field(default=0, ge=0, le=128)
    empty: int = Field(default=0, ge=0, le=128)
    permission_denied: int = Field(default=0, ge=0, le=128)
    timeout: int = Field(default=0, ge=0, le=128)
    error: int = Field(default=0, ge=0, le=128)
    truncated: int = Field(default=0, ge=0, le=128)
    reason_code: str | None = Field(default=None, max_length=128)


class CiscoRawDeviceReport(BaseModel):
    """Validated per-device payload embedded in a protected Site artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = CISCO_RAW_SCHEMA_VERSION
    run_id: str = Field(min_length=1, max_length=64)
    mode: Literal["identity", "full_collection"]
    device: CiscoRawDeviceIdentity
    connection: CiscoRawConnection
    collection: CiscoRawCollectionSummary
    outputs: dict[str, CiscoRawCommandResult] = Field(default_factory=dict)
    ignored_commands: tuple[CiscoRawIgnoredCommand, ...] = ()
    discovered_physical_interfaces: tuple[str, ...] = ()
    interface_discovery_status: Literal[
        "not_requested", "success", "empty", "failed", "limited"
    ] = "not_requested"
    started_at: datetime
    finished_at: datetime


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _redact(text: str) -> str:
    text = _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", text)
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        redacted = line
        for pattern in _SECRET_LINES:
            match = pattern.match(redacted)
            if match:
                redacted = f"{match.group(1)}<redacted>"
                break
        lines.append(redacted)
    return "\n".join(lines).strip()


def classify_command_record(
    record: dict[str, Any], *, max_output_bytes: int, platform: str | None = None,
) -> CiscoRawCommandResult:
    command = str(record.get("command") or "")[:256]
    started = _timestamp(record.get("started_at"))
    finished = _timestamp(record.get("finished_at"))
    duration_ms = max(0, int((finished - started).total_seconds() * 1000))
    raw_stdout = record.get("stdout")
    stdout = raw_stdout if isinstance(raw_stdout, str) else ""
    message = record.get("message")
    message = message if isinstance(message, str) else ""
    # A successful `show logging` can legitimately contain old timeout,
    # authorization, and invalid-command messages. Only transport/module
    # diagnostics may classify that command as an execution failure.
    inspect_stdout = not command.casefold().startswith("show logging")
    evidence = f"{message}\n{stdout if inspect_stdout else ''}".strip()
    common = dict(command=command, duration_ms=duration_ms,
                  started_at=started, finished_at=finished)
    permission_pattern = _FORTIOS_PERMISSION if platform == "fortios" else _PERMISSION
    unsupported_pattern = _FORTIOS_UNSUPPORTED if platform == "fortios" else _UNSUPPORTED
    if permission_pattern.search(evidence):
        return CiscoRawCommandResult(**common, status="permission_denied", reason_code="permission_denied")
    if _TIMEOUT.search(evidence):
        return CiscoRawCommandResult(**common, status="timeout", reason_code="command_timeout")
    if unsupported_pattern.search(evidence):
        return CiscoRawCommandResult(**common, status="unsupported", reason_code="unsupported_command")
    if record.get("failed") is True or message:
        return CiscoRawCommandResult(**common, status="error", reason_code="command_execution_error")
    cleaned = _redact(_PROMPT_ONLY.sub("", stdout)).strip()
    if not cleaned:
        return CiscoRawCommandResult(**common, status="empty", reason_code="empty_output")
    encoded = cleaned.encode("utf-8")
    if len(encoded) > max_output_bytes:
        clipped = encoded[:max_output_bytes].decode("utf-8", errors="ignore")
        return CiscoRawCommandResult(**common, status="truncated", stdout=clipped, truncated=True)
    return CiscoRawCommandResult(**common, status="success", stdout=cleaned)


_INTERFACE_ERROR_FIELDS = frozenset({
    "align_err", "fcs_err", "xmit_err", "rcv_err", "undersize",
    "outdiscards", "single_col", "multi_col", "late_col", "excess_col",
    "carri_sen", "runts", "giants",
})


def _field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def parse_cisco_interface_error_counters(text: str) -> list[dict[str, Any]]:
    """Parse IOS tabular error counters without inventing missing zeroes."""
    interfaces: dict[str, dict[str, Any]] = {}
    header: list[str] | None = None
    for raw_line in text.splitlines():
        tokens = raw_line.split()
        if not tokens:
            continue
        first = _field_name(tokens[0])
        if first in {"port", "interface"}:
            header = ["interface", *(_field_name(item) for item in tokens[1:])]
            continue
        if header is None or len(tokens) != len(header):
            continue
        if not any(character.isdigit() for character in tokens[0]):
            continue
        row = interfaces.setdefault(tokens[0], {"interface": tokens[0]})
        for field, value in zip(header[1:], tokens[1:]):
            if field not in _INTERFACE_ERROR_FIELDS:
                continue
            if value in {"--", "N/A", "n/a"}:
                row[field] = None
            elif re.fullmatch(r"\d+", value.replace(",", "")):
                row[field] = int(value.replace(",", ""))
    return list(interfaces.values())


def parse_cisco_smb_running_config(text: str) -> list[dict[str, Any]]:
    summary = _parse_cisco_smb_running_config(text)
    return [summary] if summary is not None else []


_CUSTOM_PARSERS = {
    "parse_cisco_interface_error_counters": parse_cisco_interface_error_counters,
    "parse_cisco_smb_running_config": parse_cisco_smb_running_config,
}


def _parse_textfsm_template(
    definition: ResolvedCiscoCommand, text: str,
) -> list[dict[str, Any]]:
    """Parse with the exact packaged template named by the registry."""
    import textfsm

    template_name = definition.parser.template
    if template_name is None:
        raise ValueError("TextFSM parser has no template")
    template = files("ntc_templates").joinpath("templates", template_name)
    if not template.is_file():
        raise FileNotFoundError(template_name)
    with template.open("r", encoding="utf-8") as handle:
        parser = textfsm.TextFSM(handle)
        records = parser.ParseText(text)
    headers = [str(header).casefold() for header in parser.header]
    return [dict(zip(headers, record)) for record in records]


def _normalize_rows(
    definition: ResolvedCiscoCommand, rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Provide a small common view only where field meaning is unambiguous."""
    def known(value: Any) -> Any:
        return None if value in {None, "", "--", "N/A", "n/a"} else value

    if definition.category == "hardware_inventory":
        return {
            "category": definition.category,
            "items": [{
                "name": known(row.get("name")),
                "description": known(row.get("descr")),
                "product_id": known(row.get("pid")),
                "version_id": known(row.get("vid")),
                "serial_number": known(row.get("sn")),
            } for row in rows],
        }
    if definition.id in {"interfaces_status", "interfaces_description"}:
        return {
            "category": "interfaces",
            "items": [{
                "name": known(row.get("interface") or row.get("port")),
                "description": known(row.get("description") or row.get("name")),
                "status": known(row.get("status") or row.get("linkstate")),
                "vlan_id": known(row.get("vlan_id")),
                "duplex": known(row.get("duplex")),
                "speed": known(row.get("speed")),
                "media_type": known(row.get("type")),
            } for row in rows],
        }
    if definition.category == "interface_errors":
        return {"category": definition.category, "items": rows}
    # Parser rows remain authoritative. This additive wrapper gives report
    # consumers a stable category without inventing absent values or units.
    return {"category": definition.category, "items": rows}


def parse_command_output(
    result: CiscoRawCommandResult, *, platform: str,
) -> CiscoRawCommandResult:
    """Add registry-selected structured data without changing raw state."""
    if result.status not in {"success", "truncated"} or not result.stdout:
        return result.model_copy(update={"parsing_status": "skipped"})
    definition = CISCO_COMMAND_REGISTRY.command(platform, result.command)
    if definition is None:
        return result.model_copy(update={
            "parser_status": "template_missing",
            "parsing_status": "no_parser",
        })
    common = {
        "command_id": definition.id,
        "category": definition.category,
        "parser_engine": definition.parser.engine,
        "parser_platform": definition.parser.platform,
        "parser_template": (
            definition.parser.template
            if definition.parser.engine == "ntc_textfsm"
            else f"atlas:{definition.parser.function}/1.0"
            if definition.parser.engine == "atlas_custom" else None
        ),
        "parser_version": (
            CISCO_COMMAND_REGISTRY.catalog.ntc_templates_version
            if definition.parser.engine == "ntc_textfsm" else "1.0"
            if definition.parser.engine == "atlas_custom" else None
        ),
    }
    if definition.parser.engine == "none":
        return result.model_copy(update={
            **common,
            "parser_status": "template_missing",
            "parsing_status": "no_parser",
        })
    try:
        if definition.parser.engine == "ntc_textfsm":
            rows = _parse_textfsm_template(definition, result.stdout)
        else:
            function = _CUSTOM_PARSERS.get(definition.parser.function or "")
            if function is None:
                raise ValueError("unregistered custom parser")
            rows = function(result.stdout)
    except Exception:
        return result.model_copy(update={
            **common, "parser_status": "failed", "parsing_status": "parse_error",
        })
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return result.model_copy(update={
            **common, "parser_status": "failed", "parsing_status": "parse_error",
        })
    if not rows:
        return result.model_copy(update={
            **common, "parser_status": "failed", "parsing_status": "empty_result",
        })
    allowed = set(definition.parser.output_fields)
    if any(set(row) - allowed for row in rows):
        return result.model_copy(update={
            **common, "parser_status": "failed", "parsing_status": "parse_error",
        })
    return result.model_copy(update={
        **common,
        "parser_status": "parsed",
        "parsing_status": "success",
        "structured_data": rows,
        "normalized_data": _normalize_rows(definition, rows),
    })


def safe_slug(value: object, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80] or fallback


def site_artifact_name(tenant: str, site: str, started_at: datetime) -> str:
    tenant_slug = safe_slug(tenant, "no-tenant")
    site_slug = safe_slug(site, "no-site")
    tenant_prefix = f"{tenant_slug}-"
    if site_slug.startswith(tenant_prefix):
        site_slug = site_slug.removeprefix(tenant_prefix) or "no-site"
    stamp = started_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return f"{tenant_slug}-{site_slug}-{stamp}.json"


__all__ = [
    "CISCO_FULL_COMMANDS", "CISCO_IDENTITY_COMMANDS", "CISCO_RAW_PLATFORMS",
    "CISCO_RAW_SCHEMA_VERSION", "CiscoRawCommandResult",
    "CiscoRawDeviceReport", "CiscoRawIgnoredCommand",
    "cisco_raw_commands", "classify_command_record", "parse_command_output", "safe_slug",
    "parse_cisco_interface_error_counters", "parse_cisco_smb_running_config",
    "site_artifact_name",
    "RAW_SCHEMA_VERSION",
]
