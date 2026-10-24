#!/usr/bin/python
"""Run a fixed set of read-only FortiOS CLI commands through SSH."""

from __future__ import annotations

import re
import socket
import time

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
"""


ALLOWED_COMMANDS = {
    "diagnose autoupdate status",
    "diagnose autoupdate versions",
    "diagnose debug config-error-log read",
    "diagnose debug crashlog read",
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
    "get system status",
    "get system performance status",
    "get vpn certificate local details",
    "get vpn certificate ca details",
}
PROMPT_PATTERN = re.compile(r"(?m)^[^\r\n]*[#$>]\s*$")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PAGER_MARKER = "--More--"
INTERFACE_HEADER_PATTERN = re.compile(
    r"(?m)^==\s*\[\s*([A-Za-z0-9_.:-]{1,64})\s*]\s*$"
)
MAX_DISCOVERED_INTERFACES = 256


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
        },
        supports_check_mode=True,
    )
    params = module.params
    commands = params["commands"]
    if not commands or any(command not in ALLOWED_COMMANDS for command in commands):
        module.fail_json(msg="FortiOS CLI command is not allow-listed")

    client = None
    try:
        client = _connect(params)
        channel = client.invoke_shell(width=200, height=1000)
        banner, prompt = _receive_until_prompt(
            channel,
            params["command_timeout"],
        )
        outputs = {}
        for command in commands:
            channel.send(command + "\n")
            output, returned_prompt = _receive_until_prompt(
                channel,
                params["command_timeout"],
                expected_prompt=prompt,
            )
            outputs[command] = _clean_output(output, command, returned_prompt)
        interface_diagnostics = []
        if params["collect_interface_diagnostics"]:
            interface_output = (
                outputs.get("get system interface physical", "")
                or outputs.get("get system interface", "")
            )
            discovered_interfaces = tuple(dict.fromkeys(
                INTERFACE_HEADER_PATTERN.findall(interface_output)
            ))
            for interface in discovered_interfaces[:MAX_DISCOVERED_INTERFACES]:
                command = f"diagnose hardware deviceinfo nic {interface}"
                channel.send(command + "\n")
                output, returned_prompt = _receive_until_prompt(
                    channel,
                    params["command_timeout"],
                    expected_prompt=prompt,
                )
                cleaned = _clean_output(output, command, returned_prompt)
                outputs[command] = cleaned
                interface_diagnostics.append(
                    f"===== {command} =====\n{cleaned}"
                )
        module.exit_json(
            changed=False,
            outputs=outputs,
            interface_diagnostics="\n\n".join(interface_diagnostics),
            discovered_interface_count=len(
                discovered_interfaces
                if params["collect_interface_diagnostics"]
                else ()
            ),
            interface_diagnostics_truncated=(
                params["collect_interface_diagnostics"]
                and len(discovered_interfaces) > MAX_DISCOVERED_INTERFACES
            ),
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
