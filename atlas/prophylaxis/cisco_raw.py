"""Versioned parser-tolerant Cisco CLI collection contract."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RAW_SCHEMA_VERSION = "1.0"
CISCO_RAW_SCHEMA_VERSION = RAW_SCHEMA_VERSION
CISCO_RAW_PLATFORMS = frozenset({
    "cisco-ios", "cisco-ios-xe", "cisco-nxos", "cisco-asa", "cisco-cbs",
    "cisco-cbs250-350", "cisco-catalyst-1200", "cisco-catalyst-1300",
})
CISCO_IDENTITY_COMMANDS = (
    "show version", "show inventory", "show hostname",
)
CISCO_FULL_COMMANDS = (
    "dir", "more flash:logfile.txt", "show aaa-server protocol radius",
    "show arp", "show asp drop", "show cdp neighbors", "show clock",
    "show crypto ca certificates", "show env all", "show environment",
    "show failover history", "show failover state",
    "show flash: | include crash", "show hardware", "show hostname",
    "show interfaces description", "show int status", "show interface",
    "show interfaces counters errors", "show interfaces status",
    "show interfaces transceiver", "show inventory", "show ip arp",
    "show ip int brief", "show ip route", "show license all",
    "show license summary", "show lldp neighbors", "show logging",
    "show logging last 1000", "show logging last 2000",
    "show mac address-table", "show memory", "show ntp associations",
    "show ntp peer-status", "show ntp status",
    "show platform software status control-processor brief", "show post",
    "show power inline", "show processes cpu history", "show route",
    "show running-config", "show stack", "show switch",
    "show switch stack-ports summary", "show system resources",
    "show version", "show vlan", "show vpc",
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
    re.compile(
        r"(?i)^(\s*set\s+(?:password|passwd|secret|psksecret|private-key|"
        r"community)\s+).*$"
    ),
)

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
    parser_template: str | None = Field(default=None, max_length=255)
    structured_data: list[dict[str, Any]] | None = None

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
    evidence = f"{message}\n{stdout}".strip()
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


_NTC_PLATFORM = {
    "cisco-ios": "cisco_ios",
    "cisco-ios-xe": "cisco_ios",
    "cisco-nxos": "cisco_nxos",
    "cisco-asa": "cisco_asa",
    "cisco-cbs": "cisco_s300",
    "cisco-cbs250-350": "cisco_s300",
    # Catalyst 1200/1300 share the reviewed Cisco SMB transport, but a matching
    # NTC platform/template contract has not been verified from device output.
    # Keep their raw data without silently parsing it as another platform.
}


def parse_command_output(
    result: CiscoRawCommandResult, *, platform: str,
) -> CiscoRawCommandResult:
    """Add best-effort NTC/TextFSM data without changing raw collection state."""
    if result.status not in {"success", "truncated"} or not result.stdout:
        return result
    ntc_platform = _NTC_PLATFORM.get(platform)
    if ntc_platform is None:
        return result.model_copy(update={"parser_status": "template_missing"})
    try:
        from ntc_templates.parse import parse_output
        rows = parse_output(
            platform=ntc_platform,
            command=result.command,
            data=result.stdout,
        )
    except Exception as exc:
        message = str(exc).casefold()
        parser_status = (
            "template_missing"
            if "template" in message and ("not found" in message or "no template" in message)
            else "failed"
        )
        return result.model_copy(update={"parser_status": parser_status})
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return result.model_copy(update={"parser_status": "failed"})
    if not rows:
        return result.model_copy(update={"parser_status": "failed"})
    return result.model_copy(update={
        "parser_status": "parsed",
        "parser_template": f"ntc-templates:9.3.0/{ntc_platform}/{result.command}",
        "structured_data": rows,
    })


def safe_slug(value: object, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80] or fallback


def site_artifact_name(tenant: str, site: str, started_at: datetime) -> str:
    stamp = started_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return f"{safe_slug(tenant, 'no-tenant')}-{safe_slug(site, 'no-site')}-{stamp}.json"


__all__ = [
    "CISCO_FULL_COMMANDS", "CISCO_IDENTITY_COMMANDS", "CISCO_RAW_PLATFORMS",
    "CISCO_RAW_SCHEMA_VERSION", "CiscoRawCommandResult",
    "CiscoRawDeviceReport", "CiscoRawIgnoredCommand",
    "classify_command_record", "parse_command_output", "safe_slug",
    "site_artifact_name",
    "RAW_SCHEMA_VERSION",
]
