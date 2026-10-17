"""Secret-conscious Ansible boundary for SNMP inspection and configuration."""

import os
from pathlib import Path
import tempfile

from atlas.monitoring.models import MonitoringError
from atlas.prophylaxis.ansible_backend import _failure_from_event


RESULT_TASK_SUFFIX = "ATLAS_MONITORING_RESULT"


class MonitoringAnsibleBackend:
    """Run the isolated monitoring playbook using the shared Runner policy."""

    def __init__(self, backend) -> None:
        self.backend = backend

    def execute(
        self,
        *,
        host,
        port,
        profile,
        credentials,
        operation,
        options,
        secret=None,
    ):
        if operation not in {"inspect", "configure"}:
            raise MonitoringError("invalid_monitoring_operation")
        playbook = self.backend.project_dir / "playbooks/configure_monitoring.yml"
        if (
            self.backend.project_dir.is_symlink()
            or playbook.is_symlink()
            or not playbook.is_file()
        ):
            raise MonitoringError("ansible_content_error", 503)

        captured_result = None
        captured_failure = None

        def event_handler(event):
            nonlocal captured_result, captured_failure
            event_data = event.get("event_data", {})
            task = str(event_data.get("task", ""))
            if event.get("event") == "runner_on_ok" and task.endswith(
                RESULT_TASK_SUFFIX
            ):
                result = event_data.get("res", {})
                facts = result.get("ansible_facts", {}) if isinstance(result, dict) else {}
                candidate = (
                    facts.get("atlas_monitoring_result")
                    if isinstance(facts, dict)
                    else None
                )
                if isinstance(candidate, dict):
                    captured_result = candidate
            elif event.get("event") in {"runner_on_failed", "runner_on_unreachable"}:
                captured_failure = event
            return False

        with tempfile.TemporaryDirectory(prefix="atlas-monitoring-ansible-") as temporary:
            private_dir = Path(temporary)
            os.chmod(private_dir, 0o700)
            home = self.backend._prepare_home(private_dir)
            local_tmp = private_dir / "local-tmp"
            local_tmp.mkdir(mode=0o700)
            environment = {
                "HOME": str(home),
                "USER": "atlas",
                "LOGNAME": "atlas",
                "TMPDIR": str(private_dir),
                "ANSIBLE_CONFIG": str(self.backend.project_dir / "ansible.cfg"),
                "ANSIBLE_LOCAL_TEMP": str(local_tmp),
                "ANSIBLE_HOST_KEY_CHECKING": str(
                    self.backend.strict_host_keys
                ).lower(),
                "ANSIBLE_HOST_KEY_AUTO_ADD": str(
                    not self.backend.strict_host_keys
                ).lower(),
                "SSH_AUTH_SOCK": "",
                "ATLAS_SNMP_COMMUNITY": (secret or {}).get("community", ""),
                **self.backend._credential_environment(profile, credentials),
            }
            ssh_config = None
            if profile.legacy_ciphers or profile.legacy_macs:
                ssh_config = home / ".ssh" / "config"
                lines = ["Host *"]
                if profile.legacy_ciphers:
                    lines.append("  Ciphers +" + ",".join(profile.legacy_ciphers))
                if profile.legacy_macs:
                    lines.append("  MACs +" + ",".join(profile.legacy_macs))
                ssh_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.chmod(ssh_config, 0o600)
                environment["ANSIBLE_LIBSSH_CONFIG_FILE"] = str(ssh_config)
            if options.get("ca_bundle"):
                environment["SSL_CERT_FILE"] = options["ca_bundle"]

            inventory = self.backend._inventory(host, port, profile)
            if profile.transport == "https":
                inventory["all"]["hosts"]["atlas_target"][
                    "ansible_httpapi_validate_certs"
                ] = options.get(
                    "tls_verify", self.backend.fortios_validate_certs
                )

            try:
                run = self.backend.runner(
                    private_data_dir=str(private_dir),
                    project_dir=str(self.backend.project_dir),
                    playbook="playbooks/configure_monitoring.yml",
                    roles_path=[str(self.backend.project_dir / "roles")],
                    inventory=inventory,
                    extravars={
                        "atlas_schema_version": 1,
                        "atlas_monitoring_operation": operation,
                        "atlas_platform": profile.slug,
                        "atlas_snmp_port": options["snmp_port"],
                        "atlas_monitoring_source_ips": options["source_ips"],
                        "atlas_management_interface": options.get("interface", ""),
                        "atlas_fortios_vdom": options.get("vdom", ""),
                        "atlas_community_id": (secret or {}).get("community_id", 0),
                        "atlas_aruba_aoscx_vrf": options.get(
                            "aruba_aoscx_vrf", "default"
                        ),
                        "atlas_allow_unrestricted_snmp_v2c": options.get(
                            "allow_unrestricted_snmp_v2c", False
                        ),
                    },
                    envvars=environment,
                    suppress_env_files=True,
                    event_handler=event_handler,
                    quiet=True,
                    rotate_artifacts=0,
                    timeout=self.backend.job_timeout,
                )
            except Exception:
                raise MonitoringError("ansible_execution_failed", 503) from None

            if getattr(run, "status", None) != "successful" or getattr(run, "rc", 1) != 0:
                failure = _failure_from_event(captured_failure)
                raise MonitoringError(failure.code, 503)
            if not isinstance(captured_result, dict):
                raise MonitoringError("ansible_execution_failed", 503)
            actions = captured_result.get("actions")
            if (
                captured_result.get("schema_version") != 1
                or captured_result.get("platform") != profile.slug
                or type(captured_result.get("compliant")) is not bool
                or not isinstance(actions, list)
                or any(not isinstance(action, str) or len(action) > 160 for action in actions)
            ):
                raise MonitoringError("ansible_execution_failed", 503)
            return {
                "compliant": captured_result["compliant"],
                "actions": actions,
            }


__all__ = ["MonitoringAnsibleBackend"]
