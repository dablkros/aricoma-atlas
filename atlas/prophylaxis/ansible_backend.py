"""Thin, secret-conscious Ansible Runner execution boundary."""

import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable, Iterable

from atlas.prophylaxis.errors import (
    AnsibleContentError,
    AnsibleExecutionError,
    AuthenticationFailedError,
    ConnectionFailedError,
    ConnectionTimeoutError,
    HostKeyMismatchError,
    HostKeyUnknownError,
    SSHNegotiationFailedError,
)
from atlas.prophylaxis.models import CheckId, RawCheckResult
from atlas.prophylaxis.platforms import AnsiblePlatformProfile


RESULT_TASK_SUFFIX = "ATLAS_RESULT"
logger = logging.getLogger("atlas.prophylaxis.ansible")


def _ansible_run(**kwargs):
    import ansible_runner

    return ansible_runner.run(**kwargs)


def _failure_result(event: dict | None) -> dict:
    event_data = event.get("event_data", {}) if isinstance(event, dict) else {}
    result = event_data.get("res", {}) if isinstance(event_data, dict) else {}
    return result if isinstance(result, dict) else {}


def _failure_from_event(event: dict | None):
    result = _failure_result(event)
    message = " ".join(
        str(result.get(key, "")) for key in ("msg", "exception", "stderr")
    ).lower()
    if any(
        phrase in message
        for phrase in (
            "couldn't resolve module/action",
            "could not resolve module/action",
            "the role 'atlas_check' was not found",
            "unable to retrieve file contents",
        )
    ):
        return AnsibleContentError()
    if (
        "remote host identification has changed" in message
        or ("host key for" in message and "has changed" in message)
        or "bad host key" in message
    ):
        return HostKeyMismatchError()
    if any(
        phrase in message
        for phrase in (
            "host key verification failed",
            "host key is unknown",
            "hostkeynotverifiable",
            "not found in known_hosts",
        )
    ):
        return HostKeyUnknownError()
    meta = result.get("meta", {})
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("http_status") in {401, 403} or any(
        phrase in message
        for phrase in (
            "authentication failed",
            "failed to authenticate",
            "access denied",
            "invalid access token",
            "invalid credential",
            "permission denied",
            "unauthorized",
            "wrong credentials",
        )
    ):
        return AuthenticationFailedError()
    if any(
        phrase in message
        for phrase in (
            "kex error",
            "key exchange failed",
            "no match for method",
            "no matching cipher",
            "no matching host key",
        )
    ):
        return SSHNegotiationFailedError()
    if "timed out" in message or "timeout" in message:
        return ConnectionTimeoutError()
    if (
        event
        and event.get("event") == "runner_on_unreachable"
    ) or any(
        phrase in message
        for phrase in (
            "connection reset by peer",
            "connection refused",
            "could not connect to",
            "network is unreachable",
            "no route to host",
            "name or service not known",
        )
    ):
        return ConnectionFailedError()
    return AnsibleExecutionError()


def _redacted_message(value: object, secrets: Iterable[object]) -> str:
    if not isinstance(value, str) or not value.strip():
        return "not_available"
    message = " ".join(value.split())
    for secret in secrets:
        if isinstance(secret, str) and secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(
        r"(?i)(bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)((?:access[_-]?token|token|password|passwd|secret|authorization)"
        r"\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[REDACTED]",
        message,
    )
    return message[:512]


def _failure_context(
    event: dict | None,
    *,
    secrets: Iterable[object] = (),
) -> dict[str, object]:
    if not isinstance(event, dict):
        return {
            "ansible_event": "not_captured",
            "ansible_task": "unknown",
            "ansible_message": "not_available",
        }
    event_data = event.get("event_data", {})
    event_data = event_data if isinstance(event_data, dict) else {}
    result = _failure_result(event)
    context: dict[str, object] = {
        "ansible_event": str(event.get("event") or "unknown")[:64],
        "ansible_task": str(event_data.get("task") or "unknown")[:160],
        "ansible_message": _redacted_message(result.get("msg"), secrets),
    }
    meta = result.get("meta", {})
    if isinstance(meta, dict):
        http_status = meta.get("http_status")
        if isinstance(http_status, int) and not isinstance(http_status, bool):
            context["ansible_http_status"] = http_status
        api_status = meta.get("status")
        if isinstance(api_status, str) and api_status:
            context["ansible_api_status"] = api_status[:64]
    return context


class AnsibleBackend:
    def __init__(
        self,
        project_dir: Path,
        *,
        known_hosts_file: Path,
        strict_host_keys: bool,
        connect_timeout: int,
        command_timeout: int,
        job_timeout: int,
        fortios_validate_certs: bool,
        runner: Callable = _ansible_run,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.known_hosts_file = Path(known_hosts_file)
        self.strict_host_keys = strict_host_keys
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.job_timeout = job_timeout
        self.fortios_validate_certs = fortios_validate_certs
        self.runner = runner

    def _prepare_home(self, private_dir: Path) -> Path:
        home = private_dir / "home"
        ssh = home / ".ssh"
        ssh.mkdir(parents=True, mode=0o700)
        if self.strict_host_keys:
            try:
                if self.known_hosts_file.is_symlink() or not self.known_hosts_file.is_file():
                    raise HostKeyUnknownError()
                shutil.copyfile(self.known_hosts_file, ssh / "known_hosts")
                os.chmod(ssh / "known_hosts", 0o600)
            except OSError:
                raise HostKeyUnknownError() from None
        return home

    def _inventory(
        self,
        host: str,
        profile: AnsiblePlatformProfile,
    ) -> dict:
        variables = {
            "ansible_host": host,
            "ansible_connection": profile.ansible_connection,
            "ansible_network_os": profile.ansible_network_os,
            "ansible_connect_timeout": self.connect_timeout,
            "ansible_command_timeout": self.command_timeout,
            "ansible_host_key_checking": self.strict_host_keys,
        }
        if profile.transport == "ssh":
            variables["ansible_network_cli_ssh_type"] = "libssh"
        if profile.transport == "https":
            variables.update(
                {
                    "ansible_httpapi_use_ssl": True,
                    "ansible_httpapi_validate_certs": self.fortios_validate_certs,
                    "ansible_httpapi_port": 443,
                }
            )
        return {"all": {"hosts": {"atlas_target": variables}}}

    @staticmethod
    def _credential_environment(
        profile: AnsiblePlatformProfile,
        credentials: dict,
    ) -> dict[str, str]:
        if profile.authentication == "api_token":
            return {"ATLAS_FORTIOS_API_TOKEN": credentials["api_token"]}
        return {
            "ATLAS_DEVICE_USERNAME": credentials["username"],
            "ATLAS_DEVICE_PASSWORD": credentials["password"],
            "ATLAS_DEVICE_ENABLE_PASSWORD": credentials.get("enable_password", ""),
        }

    def execute(
        self,
        *,
        host: str,
        profile: AnsiblePlatformProfile,
        check_id: CheckId,
        credentials: dict,
    ) -> RawCheckResult:
        if not profile.supports(check_id):
            return RawCheckResult(
                check=check_id,
                platform=profile.slug,
                status="unsupported",
                error_code="unsupported_check",
            )
        playbook = self.project_dir / "playbooks/run_check.yml"
        if (
            self.project_dir.is_symlink()
            or playbook.is_symlink()
            or not playbook.is_file()
        ):
            raise AnsibleExecutionError()

        captured_result = None
        captured_failure = None

        def event_handler(event: dict) -> bool:
            nonlocal captured_result, captured_failure
            event_data = event.get("event_data", {})
            task = str(event_data.get("task", ""))
            if event.get("event") == "runner_on_ok" and task.endswith(RESULT_TASK_SUFFIX):
                result = event_data.get("res", {})
                facts = result.get("ansible_facts", {}) if isinstance(result, dict) else {}
                candidate = facts.get("atlas_result") if isinstance(facts, dict) else None
                if isinstance(candidate, dict):
                    captured_result = candidate
            elif event.get("event") in {"runner_on_failed", "runner_on_unreachable"}:
                captured_failure = event
            return False

        with tempfile.TemporaryDirectory(prefix="atlas-ansible-") as temporary:
            private_dir = Path(temporary)
            os.chmod(private_dir, 0o700)
            home = self._prepare_home(private_dir)
            local_tmp = private_dir / "local-tmp"
            local_tmp.mkdir(mode=0o700)
            environment = {
                "HOME": str(home),
                # The Compose runtime uses the host Atlas UID, which may not
                # have a passwd entry inside the image. Python 3.13 then makes
                # getpass.getuser() fail unless a process identity is present.
                "USER": "atlas",
                "LOGNAME": "atlas",
                "TMPDIR": str(private_dir),
                "ANSIBLE_CONFIG": str(self.project_dir / "ansible.cfg"),
                "ANSIBLE_LOCAL_TEMP": str(local_tmp),
                "ANSIBLE_HOST_KEY_CHECKING": str(self.strict_host_keys).lower(),
                "ANSIBLE_HOST_KEY_AUTO_ADD": str(not self.strict_host_keys).lower(),
                "ANSIBLE_PRIVATE_KEY_FILE": "",
                "SSH_AUTH_SOCK": "",
                **self._credential_environment(profile, credentials),
            }
            try:
                result = self.runner(
                    private_data_dir=str(private_dir),
                    project_dir=str(self.project_dir),
                    playbook="playbooks/run_check.yml",
                    roles_path=[str(self.project_dir / "roles")],
                    inventory=self._inventory(host, profile),
                    extravars={
                        "atlas_check": check_id.value,
                        "atlas_platform": profile.slug,
                        "atlas_schema_version": 1,
                    },
                    envvars=environment,
                    suppress_env_files=True,
                    event_handler=event_handler,
                    quiet=True,
                    rotate_artifacts=0,
                    timeout=self.job_timeout,
                )
            except Exception as exc:
                logger.warning(
                    "ansible_runner_failed",
                    extra={
                        "event": "ansible_runner_failed",
                        "platform": profile.slug,
                        "check": check_id.value,
                        "reason_code": "ansible_execution_failed",
                        "exception_type": type(exc).__name__,
                    },
                )
                raise AnsibleExecutionError() from None

            if getattr(result, "status", None) != "successful" or getattr(result, "rc", 1) != 0:
                failure = _failure_from_event(captured_failure)
                logger.warning(
                    "ansible_execution_failed",
                    extra={
                        "event": "ansible_execution_failed",
                        "platform": profile.slug,
                        "check": check_id.value,
                        "reason_code": failure.code,
                        "runner_status": str(
                            getattr(result, "status", "unknown")
                        )[:32],
                        "runner_rc": getattr(result, "rc", None),
                        **_failure_context(
                            captured_failure,
                            secrets=credentials.values(),
                        ),
                    },
                )
                raise failure
            if captured_result is None:
                raise AnsibleExecutionError()
            try:
                raw_result = RawCheckResult.model_validate(captured_result)
            except ValueError:
                raise AnsibleExecutionError() from None
            if raw_result.check != check_id or raw_result.platform != profile.slug:
                raise AnsibleExecutionError()
            return raw_result


__all__ = ["AnsibleBackend"]
