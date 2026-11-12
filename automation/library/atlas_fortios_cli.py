#!/usr/bin/python
"""Run a fixed set of read-only FortiOS CLI commands through SSH."""

from __future__ import annotations

import re
import socket
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: atlas_fortios_cli
short_description: Run Atlas allow-listed read-only FortiOS CLI commands
options:
  host:
    type: str
    required: true
  port:
    type: int
    default: 22
  username:
    type: str
    required: true
  password:
    type: str
    required: true
  commands:
    type: list
    elements: str
    required: true
  collect_interface_diagnostics:
    type: bool
    default: false
  connect_timeout:
    type: int
    default: 10
  command_timeout:
    type: int
    default: 30
  host_key_checking:
    type: bool
    default: true
  known_hosts_path:
    type: path
    required: false
  result_path:
    type: path
    required: false
"""


ALLOWED_COMMANDS = {
    "diagnose autoupdate status",
    "diagnose autoupdate versions",
    "diagnose debug config-error-log read",
    "diagnose debug crashlog read",
    "diagnose fdsm image-list",
    "diagnose hardware deviceinfo disk",
    "diagnose hardware deviceinfo psu",
    "diagnose hardware deviceinfo tpm",
    "diagnose hardware sysinfo memory",
    "diagnose hardware sysinfo conserve",
    "diagnose hardware sysinfo sensor-status",
    "diagnose netlink interface list",
    "diagnose netlink interface packet-rate",
    "diagnose sys acme list-domains",
    "diagnose sys ha checksum cluster",
    "diagnose sys ha history read",
    "diagnose sys ntp status",
    "diagnose sys sdwan health-check",
    "diagnose sys session stat",
    "diagnose vpn tunnel list",
    "execute log display",
    "execute log filter category event",
    "execute log filter view-lines 50",
    "get router info bgp summary",
    "get router info bgp network",
    "get router info ospf neighbor",
    "get router info routing-table all",
    "get log fortianalyzer setting",
    "get system ha status",
    "get system fortiguard-service status",
    "get system interface",
    "get system interface physical",
    "get system arp",
    "get system status",
    "get system performance status",
    "get sys interface transceiver",
    "get vpn certificate local details",
    "get vpn certificate ca details",
}
PROMPT_PATTERN = re.compile(r"(?m)^[^\r\n]*[#$>]\s*$")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PAGER_MARKER = "--More--"
INTERFACE_BLOCK_PATTERN = re.compile(
    r"(?ms)^\s*==\s*\[\s*([A-Za-z0-9_.:-]{1,64})\s*]\s*$"
    r"(?P<body>.*?)(?=^\s*==\s*\[|\Z)"
)
INTERFACE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
NON_PHYSICAL_INTERFACE_PATTERN = re.compile(
    r"(?i)^(?:ssl\.|ipsec|gre|loopback|vlan|tun|vxlan)"
)
NON_PHYSICAL_TYPE_PATTERN = re.compile(
    r"(?im)^\s*type\s*:\s*(?:vlan|tunnel|aggregate|redundant|software-switch|hard-switch)\b"
)
DISCOVERY_FAILURE_PATTERN = re.compile(
    r"(?i)(command parse error before|invalid command|unknown command|"
    r"not a valid command|unrecognized parameter|syntax error|permission denied|"
    r"access denied|not authorized|insufficient privilege)"
)
MAX_DISCOVERED_INTERFACES = 64


class FortiOSPromptTimeout(TimeoutError):
    """The SSH session opened but the FortiOS CLI prompt did not return."""


def _receive_until_prompt(channel, timeout, expected_prompt=None):
    deadline = time.monotonic() + timeout
    chunks = []
    acknowledged_pagers = 0
    while time.monotonic() < deadline:
        if channel.recv_ready():
            chunk = channel.recv(65535)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
            text = "".join(chunks).replace("\r", "")
            pager_count = text.count(PAGER_MARKER)
            while acknowledged_pagers < pager_count:
                # FortiOS paginates some read-only `get` output even on a tall
                # pseudo-terminal. Space advances one page without changing
                # device configuration.
                channel.send(" ")
                acknowledged_pagers += 1
            matches = list(PROMPT_PATTERN.finditer(text))
            if matches:
                prompt = matches[-1].group(0).strip()
                if expected_prompt is None or prompt == expected_prompt:
                    return text, prompt
        else:
            time.sleep(0.05)
    raise FortiOSPromptTimeout("FortiOS CLI prompt timeout")


def _clean_output(text, command, prompt):
    text = ANSI_ESCAPE_PATTERN.sub("", text)
    text = re.sub(
        rf"{re.escape(PAGER_MARKER)}[ \t]*(?:(?:\x08)+[ \t]*)*",
        "",
        text,
    ).replace("\x08", "")
    lines = text.replace("\r", "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() == command:
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == prompt:
        lines.pop()
    return "\n".join(lines).strip()


def _connect(params):
    import paramiko

    client = paramiko.SSHClient()
    if params["host_key_checking"]:
        path = params.get("known_hosts_path")
        if not path:
            raise RuntimeError("host key verification failed: known_hosts unavailable")
        try:
            client.load_host_keys(path)
        except OSError as exc:
            raise RuntimeError(
                "host key verification failed: known_hosts unavailable"
            ) from exc
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=params["host"],
        port=params["port"],
        username=params["username"],
        password=params["password"],
        timeout=params["connect_timeout"],
        banner_timeout=params["connect_timeout"],
        auth_timeout=params["connect_timeout"],
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(command, started_at, *, stdout="", message="", failed=False):
    return {
        "command": command,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "stdout": stdout,
        "message": message,
        "failed": failed,
    }


def _open_session(params):
    client = _connect(params)
    channel = client.invoke_shell(width=200, height=1000)
    banner, prompt = _receive_until_prompt(channel, params["command_timeout"])
    return client, channel, banner, prompt


def _execute_command(channel, command, prompt, timeout):
    started_at = _utc_now()
    channel.send(command + "\n")
    try:
        output, returned_prompt = _receive_until_prompt(
            channel, timeout, expected_prompt=prompt,
        )
        return _record(
            command,
            started_at,
            stdout=_clean_output(output, command, returned_prompt),
        )
    except FortiOSPromptTimeout:
        return _record(
            command,
            started_at,
            message="FortiOS CLI command timed out",
            failed=True,
        )
    except (ConnectionError, OSError, socket.timeout):
        return _record(
            command,
            started_at,
            message="FortiOS CLI session was lost",
            failed=True,
        )


def _discover_interfaces(output):
    discovered = []
    for name, body in INTERFACE_BLOCK_PATTERN.findall(output):
        if not INTERFACE_NAME_PATTERN.fullmatch(name):
            continue
        if NON_PHYSICAL_INTERFACE_PATTERN.search(name):
            continue
        if NON_PHYSICAL_TYPE_PATTERN.search(body):
            continue
        if name not in discovered:
            discovered.append(name)
    return tuple(discovered)


def _interface_discovery_result(command_results):
    record = next(
        (
            candidate
            for candidate in command_results
            if candidate.get("command") == "get system interface physical"
        ),
        None,
    )
    if (
        not isinstance(record, dict)
        or record.get("failed") is True
        or DISCOVERY_FAILURE_PATTERN.search(str(record.get("stdout") or ""))
    ):
        return (), "failed"
    interfaces = _discover_interfaces(str(record.get("stdout") or ""))
    if len(interfaces) > MAX_DISCOVERED_INTERFACES:
        return interfaces[:MAX_DISCOVERED_INTERFACES], "limited"
    return interfaces, "success" if interfaces else "empty"


def _write_result(path_value, payload):
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main():
    module = AnsibleModule(
        argument_spec={
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "default": 22},
            "username": {"type": "str", "required": True},
            "password": {"type": "str", "required": True, "no_log": True},
            "commands": {"type": "list", "elements": "str", "required": True},
            "collect_interface_diagnostics": {"type": "bool", "default": False},
            "connect_timeout": {"type": "int", "default": 10},
            "command_timeout": {"type": "int", "default": 30},
            "host_key_checking": {"type": "bool", "default": True},
            "known_hosts_path": {"type": "path", "required": False},
            "result_path": {"type": "path", "required": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    commands = params["commands"]
    if not commands or any(command not in ALLOWED_COMMANDS for command in commands):
        module.fail_json(msg="FortiOS CLI command is not allow-listed")

    client = None
    try:
        client, channel, banner, prompt = _open_session(params)
        outputs = {}
        command_results = []
        for command in commands:
            result = _execute_command(
                channel, command, prompt, params["command_timeout"],
            )
            command_results.append(result)
            outputs[command] = result["stdout"]
            if result["failed"]:
                client.close()
                client = None
                try:
                    client, channel, _recovery_banner, prompt = _open_session(params)
                except Exception:
                    channel = None
                    break

        discovered_interfaces = ()
        discovery_status = "not_requested"
        if params["collect_interface_diagnostics"]:
            discovered_interfaces, discovery_status = _interface_discovery_result(
                command_results
            )
            for interface in discovered_interfaces:
                command = f"diagnose hardware deviceinfo nic {interface}"
                if channel is None:
                    command_results.append(_record(
                        command, _utc_now(), message="FortiOS CLI session was lost", failed=True,
                    ))
                    continue
                result = _execute_command(
                    channel, command, prompt, params["command_timeout"],
                )
                command_results.append(result)
                outputs[command] = result["stdout"]
                if result["failed"]:
                    client.close()
                    client = None
                    try:
                        client, channel, _recovery_banner, prompt = _open_session(params)
                    except Exception:
                        channel = None

        payload = {
            "commands": command_results,
            "discovered_physical_interfaces": list(
                discovered_interfaces
            ),
            "interface_discovery_status": discovery_status,
            "interface_diagnostics_limited": (
                discovery_status == "limited"
            ),
        }
        if params.get("result_path"):
            _write_result(params["result_path"], payload)
            module.exit_json(
                changed=False,
                result_file_written=True,
                discovered_interface_count=len(payload["discovered_physical_interfaces"]),
            )
        module.exit_json(
            changed=False,
            outputs=outputs,
            commands=command_results,
            discovered_physical_interfaces=payload["discovered_physical_interfaces"],
            interface_discovery_status=discovery_status,
            interface_diagnostics_truncated=payload["interface_diagnostics_limited"],
            banner=_clean_output(banner, "", prompt),
        )
    except ModuleNotFoundError:
        module.fail_json(msg="Failed to import the required Python library (paramiko)")
    except Exception as exc:
        try:
            import paramiko

            if isinstance(exc, paramiko.AuthenticationException):
                module.fail_json(msg="Authentication failed for FortiOS SSH user")
            if isinstance(exc, paramiko.BadHostKeyException):
                module.fail_json(msg="bad host key for FortiOS device")
            if isinstance(exc, paramiko.IncompatiblePeer):
                module.fail_json(
                    msg="SSH negotiation failed for FortiOS device",
                    error_type=type(exc).__name__,
                )
            if isinstance(exc, paramiko.SSHException):
                message = str(exc).lower()
                if "known_hosts" in message or "host key" in message:
                    module.fail_json(msg="host key verification failed")
        except ModuleNotFoundError:
            pass
        if isinstance(exc, FortiOSPromptTimeout):
            module.fail_json(msg="FortiOS CLI prompt timed out")
        if isinstance(exc, (TimeoutError, socket.timeout)):
            module.fail_json(msg="FortiOS SSH connection timed out")
        if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
            module.fail_json(msg="Could not connect to FortiOS device")
        module.fail_json(
            msg="FortiOS CLI collection failed",
            error_type=type(exc).__name__,
        )
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
