#!/usr/bin/python
"""Run a small, fixed set of read-only Check Point Gaia Clish commands."""

from __future__ import annotations

import re
import socket
import time

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: atlas_gaia_clish
short_description: Run Atlas allow-listed read-only Gaia Clish commands
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
    "show hostname",
    "show asset all",
    "show version all",
    "show uptime",
}
LEGACY_KEX_ALGORITHMS = (
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
)
LEGACY_HOST_KEY_ALGORITHMS = ("ssh-rsa",)
PROMPT_PATTERN = re.compile(r"(?m)^[^\r\n]*[>#]\s*$")


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
    raise TimeoutError("Gaia Clish prompt timeout")


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


def _append_algorithms(current, additions):
    return tuple(dict.fromkeys((*current, *additions)))


def _gaia_transport_factory(paramiko):
    def factory(sock, **kwargs):
        transport = paramiko.Transport(sock, **kwargs)
        security = transport.get_security_options()
        security.kex = _append_algorithms(
            security.kex,
            LEGACY_KEX_ALGORITHMS,
        )
        security.key_types = _append_algorithms(
            security.key_types,
            LEGACY_HOST_KEY_ALGORITHMS,
        )
        return transport

    return factory


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
        transport_factory=_gaia_transport_factory(paramiko),
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
        module.fail_json(msg="Gaia Clish command is not allow-listed")

    client = None
    try:
        client = _connect(params)
        channel = client.invoke_shell(width=200, height=1000)
        _banner, prompt = _receive_until_prompt(
            channel,
            params["command_timeout"],
        )
        if "Expert@" in prompt:
            module.fail_json(msg="Gaia account must open in Clish, not Expert mode")
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
                module.fail_json(msg="Authentication failed for Gaia Clish user")
            if isinstance(exc, paramiko.BadHostKeyException):
                module.fail_json(msg="bad host key for Gaia device")
            if isinstance(exc, paramiko.IncompatiblePeer):
                detail = str(exc).lower()
                if "kex algorithm" in detail:
                    stage = "key exchange"
                elif "host key" in detail:
                    stage = "host key"
                elif "ciphers" in detail:
                    stage = "cipher"
                elif "macs" in detail:
                    stage = "message authentication"
                else:
                    stage = "algorithm selection"
                module.fail_json(
                    msg=f"SSH negotiation failed during {stage}",
                    error_type=type(exc).__name__,
                )
            if isinstance(exc, paramiko.SSHException):
                message = str(exc).lower()
                if "known_hosts" in message or "host key" in message:
                    module.fail_json(msg="host key verification failed")
        except ModuleNotFoundError:
            pass
        if isinstance(exc, (TimeoutError, socket.timeout)):
            module.fail_json(msg="Gaia Clish connection timed out")
        if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
            module.fail_json(msg="Could not connect to Gaia device")
        module.fail_json(
            msg="Gaia Clish collection failed",
            error_type=type(exc).__name__,
        )
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
