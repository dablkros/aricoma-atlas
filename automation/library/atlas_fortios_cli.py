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
    "get system status",
    "get system performance status",
}
PROMPT_PATTERN = re.compile(r"(?m)^[^\r\n]*[#$>]\s*$")


def _receive_until_prompt(channel, timeout, expected_prompt=None):
    deadline = time.monotonic() + timeout
    chunks = []
    while time.monotonic() < deadline:
        if channel.recv_ready():
            chunk = channel.recv(65535)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
            text = "".join(chunks).replace("\r", "")
            matches = list(PROMPT_PATTERN.finditer(text))
            if matches:
                prompt = matches[-1].group(0).strip()
                if expected_prompt is None or prompt == expected_prompt:
                    return text, prompt
        else:
            time.sleep(0.05)
    raise TimeoutError("FortiOS CLI prompt timeout")


def _clean_output(text, command, prompt):
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
        _banner, prompt = _receive_until_prompt(
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
        module.exit_json(changed=False, outputs=outputs)
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
