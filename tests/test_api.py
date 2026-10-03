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
        self.assertEqual(settings.http_connect_timeout, 2.5)
        self.assertEqual(settings.http_read_timeout, 12.0)

    def test_invalid_api_prefix_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(api_prefix="api/", _env_file=None)

    def test_invalid_dependency_settings_are_rejected(self):
        invalid = (
            {"openbao_url": "ftp://bao.example.test"},
            {"netbox_url": "https://user:password@netbox.example.test"},
            {"openbao_identity_file": ""},
            {"openbao_identity_file": "../backend-identity.json"},
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
        dependencies = SimpleNamespace(
            **{
                name: FakeDependency(errors.get(name))
                for name in ("openbao", "netbox", "oxidized")
            }
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
                },
                "reasons": {},
            },
        )
        self.assertEqual(dependencies.openbao.calls, 1)
        self.assertEqual(dependencies.netbox.calls, 1)
        self.assertEqual(dependencies.oxidized.calls, 1)

    def test_health_remains_liveness_only(self):
        client, dependencies = self.client(
            openbao=DependencyError("connection_failed"),
            netbox=DependencyError("connection_failed"),
            oxidized=DependencyError("connection_failed"),
        )

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(dependencies.openbao.calls, 0)
        self.assertEqual(dependencies.netbox.calls, 0)
        self.assertEqual(dependencies.oxidized.calls, 0)

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


if __name__ == "__main__":
    unittest.main()
