#!/usr/bin/python
"""Run fixed, read-only Cisco IOS/SMB CLI commands without enable mode."""

from __future__ import annotations

import re
import socket
import time

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: atlas_cisco_readonly_cli
short_description: Run Atlas allow-listed read-only Cisco CLI commands
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
    "dir",
    "show clock",
    "show cpu utilization",
    "show crypto certificate",
    "show crypto pki certificates",
    "show environment all",
    "show fiber-ports optical-transceiver",
    "show flash: | include crash",
    "show interfaces",
    "show interfaces counters",
    "show interfaces counters errors",
    "show interfaces description",
    "show interfaces status",
    "show interfaces transceiver",
    "show inventory",
    "show ip bgp summary",
    "show ip eigrp neighbors",
    "show ip interface brief",
    "show ip ospf neighbor",
    "show ip rip peers",
    "show ip route",
    "show license all",
    "show license summary",
    "show logging",
    "show ntp associations",
    "show ntp status",
    "show post",
    "show power inline",
    "show processes cpu",
    "show processes memory sorted",
    "show redundancy",
    "show sntp status",
    "show stack",
    "show stack links",
    "show standby brief",
    "show switch",
    "show switch stack-ports summary",
    "show system",
    "show version",
}

PROMPT_PATTERN = re.compile(r"(?m)^[^\r\n]*[>#]\s*$")
USERNAME_PATTERN = re.compile(r"(?im)(?:user\s*name|username|login)\s*:\s*$")
PASSWORD_PATTERN = re.compile(r"(?im)password\s*:\s*$")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PAGER_PATTERNS = (
    re.compile(r"--More--", re.I),
    re.compile(r"More:\s*<space>", re.I),
)
LEGACY_KEX_ALGORITHMS = (
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
)
LEGACY_HOST_KEY_ALGORITHMS = ("ssh-rsa",)
LEGACY_CIPHERS = ("aes128-cbc", "aes192-cbc", "aes256-cbc", "3des-cbc")
LEGACY_MACS = ("hmac-sha1",)


class CiscoPromptTimeout(TimeoutError):
    """The SSH shell opened but a Cisco CLI prompt did not return."""


def _append_algorithms(current, additions):
    return tuple(dict.fromkeys((*current, *additions)))


def _transport_factory(paramiko):
    class AtlasCiscoTransport(paramiko.Transport):
        def auth_password(self, username, password, event=None, fallback=True):
            try:
                return super().auth_password(
                    username,
                    password,
                    event=event,
                    fallback=False,
                )
            except paramiko.AuthenticationException as password_error:
                def interactive_handler(_title, _instructions, prompts):
                    answers = []
                    for prompt, _echo in prompts:
                        normalized = prompt.lower()
                        if "user" in normalized or "login" in normalized:
                            answers.append(username)
                        elif "password" in normalized:
                            answers.append(password)
                        else:
                            answers.append("")
                    return answers

                try:
                    return self.auth_interactive(
                        username,
                        interactive_handler,
                        event=event,
                    )
                except paramiko.AuthenticationException:
                    raise password_error

    def factory(sock, **kwargs):
        transport = AtlasCiscoTransport(sock, **kwargs)
        security = transport.get_security_options()
        security.kex = _append_algorithms(security.kex, LEGACY_KEX_ALGORITHMS)
        security.key_types = _append_algorithms(
            security.key_types,
            LEGACY_HOST_KEY_ALGORITHMS,
        )
        security.ciphers = _append_algorithms(security.ciphers, LEGACY_CIPHERS)
        security.digests = _append_algorithms(security.digests, LEGACY_MACS)
        return transport

    return factory


def _receive_until_prompt(
    channel,
    timeout,
    *,
    expected_prompt=None,
    username=None,
    password=None,
):
    deadline = time.monotonic() + timeout
    chunks = []
    username_sent = False
    password_sent = False
    acknowledged_pagers = 0
    while time.monotonic() < deadline:
        if not channel.recv_ready():
            time.sleep(0.05)
            continue
        chunk = channel.recv(65535)
        if not chunk:
            break
        chunks.append(chunk.decode("utf-8", errors="replace"))
        text = "".join(chunks).replace("\r", "")
        if username is not None and not username_sent and USERNAME_PATTERN.search(text):
            channel.send(username + "\n")
            username_sent = True
            chunks = []
            continue
        if password is not None and not password_sent and PASSWORD_PATTERN.search(text):
            channel.send(password + "\n")
            password_sent = True
            chunks = []
            continue
        pager_count = sum(len(pattern.findall(text)) for pattern in PAGER_PATTERNS)
        while acknowledged_pagers < pager_count:
            channel.send(" ")
            acknowledged_pagers += 1
        matches = list(PROMPT_PATTERN.finditer(text))
        if matches:
            prompt = matches[-1].group(0).strip()
            if expected_prompt is None or prompt == expected_prompt:
                return text, prompt
    raise CiscoPromptTimeout("Cisco CLI prompt timeout")


def _clean_output(text, command, prompt):
    text = ANSI_ESCAPE_PATTERN.sub("", text).replace("\x08", "")
    for pattern in PAGER_PATTERNS:
        text = pattern.sub("", text)
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
        transport_factory=_transport_factory(paramiko),
    )
    return client


def main():
    module = AnsibleModule(
        argument_spec={
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "default": 22},
            "username": {"type": "str", "required": True, "no_log": True},
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
        module.fail_json(msg="Cisco CLI command is not allow-listed")

    client = None
    try:
        client = _connect(params)
        channel = client.invoke_shell(width=240, height=1000)
        _banner, prompt = _receive_until_prompt(
            channel,
            params["command_timeout"],
            username=params["username"],
            password=params["password"],
        )
        outputs = {}
        stdout = []
        for command in commands:
            channel.send(command + "\n")
            output, returned_prompt = _receive_until_prompt(
                channel,
                params["command_timeout"],
                expected_prompt=prompt,
            )
            cleaned = _clean_output(output, command, returned_prompt)
            outputs[command] = cleaned
            stdout.append(cleaned)
        module.exit_json(changed=False, outputs=outputs, stdout=stdout)
    except ModuleNotFoundError:
        module.fail_json(msg="Failed to import the required Python library (paramiko)")
    except Exception as exc:
        try:
            import paramiko

            if isinstance(exc, paramiko.AuthenticationException):
                module.fail_json(msg="Authentication failed for Cisco SSH user")
            if isinstance(exc, paramiko.BadHostKeyException):
                module.fail_json(msg="bad host key for Cisco device")
            if isinstance(exc, paramiko.IncompatiblePeer):
                module.fail_json(
                    msg="SSH negotiation failed for Cisco device",
                    error_type=type(exc).__name__,
                )
            if isinstance(exc, paramiko.SSHException):
                message = str(exc).lower()
                if "known_hosts" in message or "host key" in message:
                    module.fail_json(msg="host key verification failed")
        except ModuleNotFoundError:
            pass
        if isinstance(exc, CiscoPromptTimeout):
            module.fail_json(msg="Cisco CLI prompt timed out")
        if isinstance(exc, (TimeoutError, socket.timeout)):
            module.fail_json(msg="Cisco SSH connection timed out")
        if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
            module.fail_json(msg="Could not connect to Cisco device")
        module.fail_json(
            msg="Cisco CLI collection failed",
            error_type=type(exc).__name__,
        )
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
