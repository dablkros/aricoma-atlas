import json
import logging
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas import __version__
from atlas.api.main import create_app
from atlas.config import Settings
from atlas.logging import JsonFormatter


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

    def test_invalid_api_prefix_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(api_prefix="api/", _env_file=None)


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


if __name__ == "__main__":
    unittest.main()
