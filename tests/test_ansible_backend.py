import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2.nativetypes import NativeEnvironment
import yaml

from atlas.prophylaxis.ansible_backend import (
    AnsibleBackend,
    _failure_context,
    _failure_from_event,
)
from atlas.monitoring.ansible import MonitoringAnsibleBackend
from atlas.prophylaxis.errors import (
    AnsibleContentError,
    AuthenticationFailedError,
    ConnectionFailedError,
    SSHNegotiationFailedError,
)
from atlas.prophylaxis.models import CheckId
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry


ROOT = Path(__file__).resolve().parent.parent


class CapturingRunner:
    def __init__(self, raw_result):
        self.raw_result = raw_result
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["event_handler"](
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "atlas_check : ATLAS_RESULT",
                    "res": {"ansible_facts": {"atlas_result": self.raw_result}},
                },
            }
        )
        return SimpleNamespace(status="successful", rc=0)


class AnsibleBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.known_hosts = Path(self.temporary.name) / "known_hosts"
        self.known_hosts.write_text("fixture host key\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def backend(self, runner, *, fortios_validate_certs=True):
        return AnsibleBackend(
            ROOT / "automation",
            known_hosts_file=self.known_hosts,
            strict_host_keys=True,
            connect_timeout=10,
            command_timeout=20,
            job_timeout=120,
            fortios_validate_certs=fortios_validate_certs,
            runner=runner,
        )

    def test_runtime_inventory_is_one_host_and_secret_free(self):
        runner = CapturingRunner(
            {
                "schema_version": 1,
                "check": "cpu_utilization",
                "platform": "cisco-ios",
                "status": "ok",
                "data": None,
                "raw_output": "CPU output",
                "error_code": None,
            }
        )
        profile = AnsiblePlatformRegistry().resolve("cisco-ios")

        result = self.backend(runner).execute(
            host="192.0.2.10",
            port=22,
            profile=profile,
            check_id=CheckId.CPU_UTILIZATION,
            credentials={"username": "admin", "password": "secret"},
        )

        call = runner.calls[0]
        self.assertEqual(result.raw_output, "CPU output")
        self.assertEqual(
            list(call["inventory"]["all"]["hosts"]),
            ["atlas_target"],
        )
        self.assertNotIn("secret", repr(call["inventory"]))
        self.assertEqual(
            call["inventory"]["all"]["hosts"]["atlas_target"]["ansible_port"],
            22,
        )
        self.assertEqual(
            call["roles_path"],
            [str(ROOT / "automation" / "roles")],
        )
        self.assertEqual(call["envvars"]["USER"], "atlas")
        self.assertEqual(call["envvars"]["LOGNAME"], "atlas")
        self.assertNotIn("ANSIBLE_PRIVATE_KEY_FILE", call["envvars"])
        self.assertTrue(call["suppress_env_files"])
        self.assertFalse(Path(call["private_data_dir"]).exists())

    def test_fortios_uses_httpapi_and_token_environment(self):
        runner = CapturingRunner(
            {
                "schema_version": 1,
                "check": "cpu_utilization",
                "platform": "fortios",
                "status": "ok",
                "data": {"cpu": [{"current": 5}]},
                "raw_output": None,
                "error_code": None,
            }
        )
        profile = AnsiblePlatformRegistry().resolve("fortios")

        self.backend(runner).execute(
            host="192.0.2.20",
            port=444,
            profile=profile,
            check_id=CheckId.CPU_UTILIZATION,
            credentials={"api_token": "token-value"},
        )

        call = runner.calls[0]
        host = call["inventory"]["all"]["hosts"]["atlas_target"]
        self.assertEqual(host["ansible_connection"], "ansible.netcommon.httpapi")
        self.assertTrue(host["ansible_httpapi_validate_certs"])
        self.assertEqual(host["ansible_httpapi_port"], 444)
        self.assertEqual(
            call["envvars"]["ATLAS_FORTIOS_API_TOKEN"],
            "token-value",
        )
        self.assertNotIn("token-value", repr(call["extravars"]))

    def test_fortios_can_disable_certificate_validation(self):
        runner = CapturingRunner(
            {
                "schema_version": 1,
                "check": "device_facts",
                "platform": "fortios",
                "status": "ok",
                "data": {"hostname": "FGT"},
                "raw_output": None,
                "error_code": None,
            }
        )
        profile = AnsiblePlatformRegistry().resolve("fortios")

        self.backend(runner, fortios_validate_certs=False).execute(
            host="192.0.2.20",
            port=444,
            profile=profile,
            check_id=CheckId.DEVICE_FACTS,
            credentials={"api_token": "token-value"},
        )

        host = runner.calls[0]["inventory"]["all"]["hosts"]["atlas_target"]
        self.assertFalse(host["ansible_httpapi_validate_certs"])

    def test_monitoring_playbook_keeps_community_out_of_inventory_and_extravars(self):
        class MonitoringRunner:
            def __init__(self):
                self.calls = []

            def __call__(self, **kwargs):
                self.calls.append(kwargs)
                kwargs["event_handler"](
                    {
                        "event": "runner_on_ok",
                        "event_data": {
                            "task": "atlas_monitoring : ATLAS_MONITORING_RESULT",
                            "res": {
                                "ansible_facts": {
                                    "atlas_monitoring_result": {
                                        "schema_version": 1,
                                        "platform": "cisco-ios-xe",
                                        "compliant": True,
                                        "actions": [],
                                    }
                                }
                            },
                        },
                    }
                )
                return SimpleNamespace(status="successful", rc=0)

        runner = MonitoringRunner()
        shared = self.backend(runner)
        profile = AnsiblePlatformRegistry().resolve("cisco-ios-xe")
        result = MonitoringAnsibleBackend(shared).execute(
            host="192.0.2.30",
            port=22,
            profile=profile,
            credentials={"username": "admin", "password": "device-secret"},
            operation="inspect",
            options={"snmp_port": 161, "source_ips": ["192.0.2.5"]},
            secret={"community": "SnmpSecretValue"},
        )

        call = runner.calls[0]
        self.assertTrue(result["compliant"])
        self.assertEqual(call["playbook"], "playbooks/configure_monitoring.yml")
        self.assertNotIn("SnmpSecretValue", repr(call["inventory"]))
        self.assertNotIn("SnmpSecretValue", repr(call["extravars"]))
        self.assertEqual(call["envvars"]["ATLAS_SNMP_COMMUNITY"], "SnmpSecretValue")
        self.assertTrue(call["suppress_env_files"])

    def test_cisco_ios_compliance_accepts_both_acl_host_renderings(self):
        tasks = yaml.safe_load(
            (
                ROOT
                / "automation"
                / "roles"
                / "atlas_monitoring"
                / "tasks"
                / "cisco_ios.yml"
            ).read_text(encoding="utf-8")
        )
        facts = next(
            task["ansible.builtin.set_fact"]
            for task in tasks
            if task.get("name")
            == "Derive Cisco IOS compliance without returning configuration"
        )
        template = NativeEnvironment().from_string(
            facts["atlas_monitoring_compliant"]
        )
        community_output = (
            "snmp-server community SnmpSecretValue RO ATLAS-SNMP-V2C"
        )

        for permit_line in (
            " permit host 10.200.200.10",
            " permit 10.200.200.10",
        ):
            with self.subTest(permit_line=permit_line):
                compliant = template.render(
                    atlas_snmp_community="SnmpSecretValue",
                    atlas_monitoring_source_ips=["10.200.200.10"],
                    atlas_cisco_ios_snmp={
                        "stdout": [
                            "ip access-list standard ATLAS-SNMP-V2C\n"
                            " remark Managed by Aricoma Atlas\n"
                            f"{permit_line}\n!",
                            community_output,
                        ]
                    },
                )
                self.assertIs(compliant, True)

        non_compliant = template.render(
            atlas_snmp_community="SnmpSecretValue",
            atlas_monitoring_source_ips=["10.200.200.10"],
            atlas_cisco_ios_snmp={
                "stdout": [
                    "ip access-list standard ATLAS-SNMP-V2C\n"
                    " remark Managed by Aricoma Atlas\n"
                    " permit 10.200.200.100\n!",
                    community_output,
                ]
            },
        )
        self.assertIs(non_compliant, False)

    def test_aruba_aoscx_compliance_accepts_device_canonical_rendering(self):
        tasks = yaml.safe_load(
            (
                ROOT
                / "automation"
                / "roles"
                / "atlas_monitoring"
                / "tasks"
                / "aruba_aoscx.yml"
            ).read_text(encoding="utf-8")
        )
        facts = next(
            task["ansible.builtin.set_fact"]
            for task in tasks
            if task.get("name")
            == "Derive Aruba AOS-CX compliance without returning configuration"
        )
        template = NativeEnvironment().from_string(
            facts["atlas_monitoring_compliant"]
        )
        base_output = (
            "snmp-server vrf default\n"
            "access-list ip ATLAS-SNMP-V2C\n"
            "    10 comment Managed by Aricoma Atlas\n"
            "    {permit_line}\n"
            "snmp-server community SnmpSecretValue\n"
            "    access-list ipv4 ATLAS-SNMP-V2C\n"
        )

        for permit_line in (
            "20 permit any 10.200.200.10/32 any",
            "20 permit any 10.200.200.10 any",
        ):
            with self.subTest(permit_line=permit_line):
                compliant = template.render(
                    atlas_aruba_aoscx_vrf="default",
                    atlas_snmp_community="SnmpSecretValue",
                    atlas_monitoring_source_ips=["10.200.200.10"],
                    atlas_aruba_aoscx_snmp={
                        "stdout": [base_output.format(permit_line=permit_line)]
                    },
                )
                self.assertIs(compliant, True)

        non_compliant = template.render(
            atlas_aruba_aoscx_vrf="default",
            atlas_snmp_community="SnmpSecretValue",
            atlas_monitoring_source_ips=["10.200.200.10"],
            atlas_aruba_aoscx_snmp={
                "stdout": [
                    base_output.format(
                        permit_line="20 permit any 10.200.200.100 any"
                    )
                ]
            },
        )
        self.assertIs(non_compliant, False)

    def test_failure_classification_covers_safe_network_categories(self):
        cases = (
            (
                "Failed to authenticate password: Access denied",
                AuthenticationFailedError,
            ),
            (
                "kex error: no match for method kex algos",
                SSHNegotiationFailedError,
            ),
            (
                "the role 'atlas_check' was not found",
                AnsibleContentError,
            ),
            (
                "Could not connect to https://192.0.2.20:443: "
                "[Errno 104] Connection reset by peer",
                ConnectionFailedError,
            ),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                failure = _failure_from_event(
                    {
                        "event": "runner_on_failed",
                        "event_data": {"res": {"msg": message}},
                    }
                )
                self.assertIsInstance(failure, expected)

    def test_failure_context_redacts_secret_and_keeps_safe_http_status(self):
        context = _failure_context(
            {
                "event": "runner_on_failed",
                "event_data": {
                    "task": "Collect FortiOS CPU utilization through the monitor API",
                    "res": {
                        "msg": "request failed with access_token=secret-token",
                        "meta": {"http_status": 403, "status": "error"},
                    },
                },
            },
            secrets=("secret-token",),
        )

        self.assertEqual(context["ansible_http_status"], 403)
        self.assertEqual(context["ansible_api_status"], "error")
        self.assertNotIn("secret-token", repr(context))
        self.assertIn("[REDACTED]", context["ansible_message"])

    def test_fortios_http_auth_failure_is_classified(self):
        failure = _failure_from_event(
            {
                "event": "runner_on_failed",
                "event_data": {
                    "res": {
                        "msg": "Error in repo",
                        "meta": {"http_status": 403, "status": "error"},
                    }
                },
            }
        )

        self.assertIsInstance(failure, AuthenticationFailedError)


if __name__ == "__main__":
    unittest.main()
