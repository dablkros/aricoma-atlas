import json
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas import __version__
from atlas.api.main import create_app
from atlas.config import Settings
from atlas.logging import JsonFormatter
from atlas.services.errors import DependencyError
from atlas.services.oxidized_operations import OxidizedOperationFailure
from atlas.services.oxidized_sync import OxidizedSyncFailure
from atlas.services.platform_status import PlatformStatusService


def test_settings(**overrides):
    values = {
        "environment": "test",
        "log_level": "CRITICAL",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


class SettingsTests(unittest.TestCase):
    def test_environment_variables_use_atlas_prefix(self):
        with patch.dict(
            os.environ,
            {
                "ATLAS_APP_NAME": "Atlas Test",
                "ATLAS_API_PREFIX": "/internal/api",
                "ATLAS_LOG_LEVEL": "warning",
            },
            clear=False,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.app_name, "Atlas Test")
        self.assertEqual(settings.api_prefix, "/internal/api")
        self.assertEqual(settings.log_level, "WARNING")

    def test_dependency_settings_use_safe_atlas_environment_variables(self):
        with patch.dict(
            os.environ,
            {
                "ATLAS_OPENBAO_URL": "https://bao.example.test:8200",
                "ATLAS_OPENBAO_IDENTITY_FILE": "/run/secrets/atlas-backend.json",
                "ATLAS_NETBOX_URL": "https://netbox.example.test",
                "ATLAS_OXIDIZED_URL": "http://atlas-oxidized:8888",
                "ATLAS_ZABBIX_URL": "http://atlas-zabbix-web:8080/api_jsonrpc.php",
                "ATLAS_NETBOX_UI_URL": "https://netbox.example.test",
                "ATLAS_OXIDIZED_UI_URL": "https://oxidized.example.test",
                "ATLAS_ZABBIX_UI_URL": "https://zabbix.example.test",
                "ATLAS_OXIDIZED_INVENTORY_FILE": "/run/atlas/oxidized/router.json",
                "ATLAS_PROPHYLAXIS_RESULTS_FILE": "/run/atlas/prophylaxis/results.sqlite3",
                "ATLAS_PROPHYLAXIS_RESULT_RETENTION": "5000",
                "ATLAS_ANSIBLE_PROJECT_DIR": "/app/automation",
                "ATLAS_ANSIBLE_JOB_TIMEOUT": "90",
                "ATLAS_SSH_KNOWN_HOSTS_FILE": "/run/atlas/ssh/known_hosts",
                "ATLAS_SSH_STRICT_HOST_KEYS": "false",
                "ATLAS_SSH_CONNECT_TIMEOUT": "4",
                "ATLAS_SSH_COMMAND_TIMEOUT": "30",
                "ATLAS_FORTIOS_VALIDATE_CERTS": "true",
                "ATLAS_HTTP_CONNECT_TIMEOUT": "2.5",
                "ATLAS_HTTP_READ_TIMEOUT": "12",
            },
            clear=False,
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(str(settings.openbao_url), "https://bao.example.test:8200/")
        self.assertEqual(
            settings.openbao_identity_file,
            Path("/run/secrets/atlas-backend.json"),
        )
        self.assertEqual(str(settings.netbox_url), "https://netbox.example.test/")
        self.assertEqual(str(settings.oxidized_url), "http://atlas-oxidized:8888/")
        self.assertEqual(
            str(settings.zabbix_url),
            "http://atlas-zabbix-web:8080/api_jsonrpc.php",
        )
        self.assertEqual(
            str(settings.zabbix_ui_url),
            "https://zabbix.example.test/",
        )
        self.assertEqual(
            settings.oxidized_inventory_file,
            Path("/run/atlas/oxidized/router.json"),
        )
        self.assertEqual(
            settings.prophylaxis_results_file,
            Path("/run/atlas/prophylaxis/results.sqlite3"),
        )
        self.assertEqual(settings.prophylaxis_result_retention, 5000)
        self.assertEqual(settings.ansible_project_dir, Path("/app/automation"))
        self.assertEqual(settings.ansible_job_timeout, 90)
        self.assertEqual(
            settings.ssh_known_hosts_file,
            Path("/run/atlas/ssh/known_hosts"),
        )
        self.assertFalse(settings.ssh_strict_host_keys)
        self.assertEqual(settings.ssh_connect_timeout, 4.0)
        self.assertEqual(settings.ssh_command_timeout, 30.0)
        self.assertTrue(settings.fortios_validate_certs)
        self.assertEqual(settings.http_connect_timeout, 2.5)
        self.assertEqual(settings.http_read_timeout, 12.0)

    def test_invalid_api_prefix_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(api_prefix="api/", _env_file=None)

    def test_invalid_dependency_settings_are_rejected(self):
        invalid = (
            {"openbao_url": "ftp://bao.example.test"},
            {"netbox_url": "https://user:password@netbox.example.test"},
            {"zabbix_ui_url": "http://zabbix.example.test"},
            {"zabbix_ui_url": "https://user@zabbix.example.test"},
            {"zabbix_ui_url": "https://zabbix.example.test/login"},
            {"openbao_identity_file": ""},
            {"openbao_identity_file": "../backend-identity.json"},
            {"oxidized_inventory_file": "../router.json"},
            {"prophylaxis_results_file": "../results.sqlite3"},
            {"prophylaxis_result_retention": 99},
            {"ansible_project_dir": "../automation"},
            {"ansible_job_timeout": 0},
            {"ssh_known_hosts_file": "../known_hosts"},
            {"ssh_connect_timeout": 0},
            {"ssh_command_timeout": 301},
            {"http_connect_timeout": 0},
            {"http_read_timeout": 121},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(_env_file=None, **values)

    def test_env_example_contains_paths_and_urls_but_no_secret_values(self):
        content = (
            Path(__file__).resolve().parents[1] / ".env.example"
        ).read_text(encoding="utf-8")

        self.assertIn("ATLAS_OPENBAO_IDENTITY_FILE=", content)
        self.assertIn("ATLAS_NETBOX_URL=", content)
        self.assertIn("ATLAS_ZABBIX_UI_URL=", content)
        for forbidden in (
            "ATLAS_OPENBAO_TOKEN=",
            "ATLAS_NETBOX_TOKEN=",
            "ROLE_ID=",
            "SECRET_ID=",
            "PASSWORD=",
        ):
            self.assertNotIn(forbidden, content.upper())


class LoggingTests(unittest.TestCase):
    def test_json_formatter_emits_fields_and_redacts_secret_keys(self):
        record = logging.LogRecord(
            name="atlas.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="structured event",
            args=(),
            exc_info=None,
        )
        record.request_id = "request-123"
        record.metadata = {"token": "do-not-log", "device": "router-1"}

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual(payload["message"], "structured event")
        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["metadata"]["token"], "[REDACTED]")
        self.assertEqual(payload["metadata"]["device"], "router-1")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(test_settings())
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "Aricoma Atlas",
                "version": __version__,
                "environment": "test",
            },
        )
        self.assertRegex(response.headers["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_operations_ui_and_static_assets_are_served(self):
        page = self.client.get("/")
        script = self.client.get("/static/app.js")
        styles = self.client.get("/static/styles.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn("ARICOMA ATLAS", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(styles.status_code, 200)
        self.assertEqual(script.headers["Cache-Control"], "no-store")
        self.assertEqual(styles.headers["Cache-Control"], "no-store")
        self.assertIn("/static/app.js?v=20261009-1", page.text)
        self.assertIn("/static/styles.css?v=20261009-1", page.text)
        self.assertIn('data-component="zabbix"', page.text)
        self.assertIn('data-component-link="zabbix"', page.text)
        self.assertIn("/api/prophylaxis/devices", script.text)
        self.assertNotIn("docker.sock", page.text + script.text)
        self.assertNotIn("subprocess", script.text)

    def test_not_found_uses_stable_error_contract_and_request_id(self):
        response = self.client.get(
            "/api/missing",
            headers={"X-Request-ID": "request-123"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Request-ID"], "request-123")
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "not_found",
                    "message": "Not Found",
                    "request_id": "request-123",
                }
            },
        )

    def test_unhandled_exception_does_not_leak_internal_detail(self):
        @self.app.get("/api/_test/error")
        async def fail_for_test():
            raise RuntimeError("sensitive internal detail")

        with self.assertLogs("atlas.api", level="ERROR") as logs:
            response = self.client.get(
                "/api/_test/error",
                headers={"X-Request-ID": "failure-123"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Request-ID"], "failure-123")
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                    "request_id": "failure-123",
                }
            },
        )
        self.assertNotIn("sensitive internal detail", response.text)
        self.assertNotIn("sensitive internal detail", "\n".join(logs.output))


class FakeDependency:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def check_ready(self):
        self.calls += 1
        if self.error:
            raise self.error


class ReadinessApiTests(unittest.TestCase):
    def client(self, **errors):
        services = {
            name: FakeDependency(errors.get(name))
            for name in ("openbao", "netbox", "oxidized", "zabbix")
        }
        dependencies = SimpleNamespace(**services)
        dependencies.platform_status = PlatformStatusService(
            dependencies.openbao,
            dependencies.netbox,
            dependencies.oxidized,
            dependencies.zabbix,
        )
        app = create_app(test_settings(), dependencies=dependencies)
        return TestClient(app, raise_server_exceptions=False), dependencies

    def test_all_dependencies_ready_returns_200(self):
        client, dependencies = self.client()

        response = client.get("/api/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "dependencies": {
                    "openbao": "ok",
                    "netbox": "ok",
                    "oxidized": "ok",
                    "zabbix": "ok",
                },
                "reasons": {},
            },
        )
        self.assertEqual(dependencies.openbao.calls, 1)
        self.assertEqual(dependencies.netbox.calls, 1)
        self.assertEqual(dependencies.oxidized.calls, 1)
        self.assertEqual(dependencies.zabbix.calls, 1)

    def test_health_remains_liveness_only(self):
        client, dependencies = self.client(
            openbao=DependencyError("connection_failed"),
            netbox=DependencyError("connection_failed"),
            oxidized=DependencyError("connection_failed"),
            zabbix=DependencyError("connection_failed"),
        )

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(dependencies.openbao.calls, 0)
        self.assertEqual(dependencies.netbox.calls, 0)
        self.assertEqual(dependencies.oxidized.calls, 0)
        self.assertEqual(dependencies.zabbix.calls, 0)

    def test_dependency_failure_returns_503_and_safe_reason(self):
        client, _dependencies = self.client(
            netbox=DependencyError("connection_failed")
        )

        response = client.get("/api/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["dependencies"]["netbox"], "error")
        self.assertEqual(response.json()["reasons"], {"netbox": "connection_failed"})

    def test_unexpected_exception_cannot_leak_secrets(self):
        secret = "nbt_sensitive-token password=private"
        client, _dependencies = self.client(openbao=RuntimeError(secret))

        response = client.get("/api/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["reasons"], {"openbao": "unavailable"})
        self.assertNotIn(secret, response.text)
        self.assertNotIn("password", response.text)


class FakeSync:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def sync(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def sync_result(
    status="success",
    changed=True,
    updated=True,
    reloaded=True,
    skipped=0,
    errors=0,
    issues=(),
):
    return SimpleNamespace(
        status=status,
        inventory_changed=changed,
        inventory_updated=updated,
        oxidized_reloaded=reloaded,
        summary=SimpleNamespace(
            total=1,
            added=1 if changed else 0,
            updated=0,
            removed=0,
            unchanged=0 if changed else 1,
            skipped=skipped,
            errors=errors,
        ),
        added=("SW01",) if changed else (),
        updated=(),
        removed=(),
        issues=tuple(issues),
    )


class OxidizedSyncApiTests(unittest.TestCase):
    def client(self, sync):
        dependencies = SimpleNamespace(
            openbao=FakeDependency(),
            netbox=FakeDependency(),
            oxidized=FakeDependency(),
            oxidized_sync=sync,
        )
        return TestClient(
            create_app(test_settings(), dependencies=dependencies),
            raise_server_exceptions=False,
        )

    def test_sync_success_partial_and_no_changes(self):
        issue = SimpleNamespace(
            device="SW02",
            code="missing_credential_profile",
        )
        cases = (
            (sync_result(), 200, "success"),
            (
                sync_result(
                    status="partial_success",
                    skipped=1,
                    issues=(issue,),
                ),
                200,
                "partial_success",
            ),
            (
                sync_result(
                    changed=False,
                    updated=False,
                    reloaded=False,
                ),
                200,
                "success",
            ),
        )
        for result, status_code, expected_status in cases:
            with self.subTest(expected_status=expected_status, changed=result.inventory_changed):
                response = self.client(FakeSync(result=result)).post(
                    "/api/oxidized/sync"
                )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["status"], expected_status)

    def test_sync_conflict_and_global_failures_use_safe_error_contract(self):
        cases = (
            ("sync_already_running", 409),
            ("netbox_unavailable", 503),
            ("openbao_authentication_failed", 503),
            ("inventory_invalid", 500),
        )
        for code, status_code in cases:
            with self.subTest(code=code):
                response = self.client(
                    FakeSync(error=OxidizedSyncFailure(code, status_code))
                ).post("/api/oxidized/sync")
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["error"]["code"], code)

    def test_reload_failure_reports_inventory_was_written(self):
        issue = SimpleNamespace(device=None, code="oxidized_reload_failed")
        response = self.client(
            FakeSync(
                result=sync_result(
                    status="error",
                    changed=True,
                    updated=True,
                    reloaded=False,
                    issues=(issue,),
                )
            )
        ).post("/api/oxidized/sync")

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["inventory_updated"])
        self.assertFalse(response.json()["oxidized_reloaded"])

    def test_sync_response_never_contains_secrets(self):
        response = self.client(FakeSync(result=sync_result())).post(
            "/api/oxidized/sync"
        )
        serialized = response.text.lower()
        for forbidden in ("password", "secret_id", "nbt_sensitive", "openbao-token"):
            self.assertNotIn(forbidden, serialized)


class FakeOperations:
    def __init__(self, error=None):
        self.error = error
        self.queued = []

    def status(self):
        if self.error:
            raise self.error
        return SimpleNamespace(
            status="healthy",
            netbox_enabled_devices=2,
            runtime_inventory_devices=2,
            inventory_issues=0,
        )

    def list_devices(self):
        if self.error:
            raise self.error
        return [
            SimpleNamespace(name="SW01", ip="192.0.2.1", model="ios"),
            SimpleNamespace(name="FW01", ip="192.0.2.2", model="fortigate"),
        ]

    def queue_backup(self, device):
        if self.error:
            raise self.error
        if device != "SW01":
            raise OxidizedOperationFailure("device_not_found", 404)
        self.queued.append(device)
        return SimpleNamespace(name=device, ip="192.0.2.1", model="ios")


class OperationsApiTests(unittest.TestCase):
    def client(self, operations=None, dependency_error=None, settings=None):
        openbao = FakeDependency()
        netbox = FakeDependency(dependency_error)
        oxidized = FakeDependency()
        zabbix = FakeDependency()
        dependencies = SimpleNamespace(
            openbao=openbao,
            netbox=netbox,
            oxidized=oxidized,
            zabbix=zabbix,
            platform_status=PlatformStatusService(
                openbao,
                netbox,
                oxidized,
                zabbix,
            ),
            oxidized_operations=operations or FakeOperations(),
        )
        return TestClient(
            create_app(settings or test_settings(), dependencies=dependencies),
            raise_server_exceptions=False,
        )

    def test_platform_and_oxidized_status_are_ui_ready(self):
        client = self.client()

        platform = client.get("/api/status")
        oxidized = client.get("/api/oxidized/status")

        self.assertEqual(platform.status_code, 200)
        self.assertEqual(platform.json()["status"], "healthy")
        self.assertIsNone(platform.json()["components"]["zabbix"]["url"])
        self.assertEqual(oxidized.status_code, 200)
        self.assertEqual(oxidized.json()["runtime_inventory_devices"], 2)

    def test_platform_status_exposes_validated_ui_origins(self):
        settings = test_settings(
            netbox_ui_url="https://netbox.example.test/",
            oxidized_ui_url="https://oxidized.example.test/",
            zabbix_ui_url="https://zabbix.example.test/",
        )

        response = self.client(settings=settings).get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["components"]["zabbix"]["url"],
            "https://zabbix.example.test/",
        )

    def test_platform_status_is_degraded_without_raw_dependency_detail(self):
        secret = "password=sensitive-token"
        response = self.client(
            dependency_error=RuntimeError(secret)
        ).get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertNotIn(secret, response.text)
        self.assertNotIn("reason", response.text)

    def test_device_list_is_explicitly_secret_free(self):
        response = self.client().get("/api/oxidized/devices")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()[0],
            {"name": "SW01", "ip": "192.0.2.1", "model": "ios"},
        )
        for forbidden in (
            "username",
            "password",
            "enable",
            "token",
            "secret_id",
        ):
            self.assertNotIn(forbidden, response.text.lower())

    def test_backup_valid_unknown_invalid_and_unavailable(self):
        operations = FakeOperations()
        accepted = self.client(operations).post(
            "/api/oxidized/devices/SW01/backup"
        )
        unknown = self.client(operations).post(
            "/api/oxidized/devices/UNKNOWN/backup"
        )
        invalid = self.client(
            FakeOperations(
                OxidizedOperationFailure("invalid_device_name", 422)
            )
        ).post("/api/oxidized/devices/bad%25name/backup")
        unavailable = self.client(
            FakeOperations(
                OxidizedOperationFailure("oxidized_unavailable", 503)
            )
        ).post("/api/oxidized/devices/SW01/backup")

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(
            accepted.json(),
            {"status": "accepted", "device": "SW01"},
        )
        self.assertEqual(operations.queued, ["SW01"])
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(unavailable.status_code, 503)


if __name__ == "__main__":
    unittest.main()
