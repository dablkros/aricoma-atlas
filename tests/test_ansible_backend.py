import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from atlas.prophylaxis.ansible_backend import (
    AnsibleBackend,
    _failure_context,
    _failure_from_event,
)
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

    def backend(self, runner):
        return AnsibleBackend(
            ROOT / "automation",
            known_hosts_file=self.known_hosts,
            strict_host_keys=True,
            connect_timeout=10,
            command_timeout=20,
            job_timeout=120,
            fortios_validate_certs=True,
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
