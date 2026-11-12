from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.api.routes.prophylaxis import _report_summary
from atlas.config import Settings
from atlas.prophylaxis.models import CPUUtilizationValues, StoredCPUCheckResult
from atlas.prophylaxis.store import ResultStoreError


def settings():
    return Settings(environment="test", log_level="CRITICAL", _env_file=None)


def stored_result():
    return StoredCPUCheckResult(
        id=1,
        collected_at=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
        device="SW01",
        platform="cisco-ios-xe",
        status="ok",
        values=CPUUtilizationValues(current_percent=8),
    )


class FakeProphylaxis:
    def __init__(self, error=None):
        self.error = error
        self.checked = []

    def list_devices(self):
        return [
            SimpleNamespace(
                id=10,
                name="SW01",
                ip="192.0.2.10",
                platform="cisco-ios-xe",
            )
        ]

    def run_cpu_check(self, identifier):
        if self.error:
            raise self.error
        self.checked.append(identifier)
        return stored_result()

    def result_history(self, limit):
        if self.error:
            raise self.error
        return [stored_result()][:limit]


class ProphylaxisApiTests(unittest.TestCase):
    def client(self, service, *, app_settings=None):
        dependencies = SimpleNamespace(prophylaxis=service)
        return TestClient(
            create_app(app_settings or settings(), dependencies=dependencies),
            raise_server_exceptions=False,
        )

    def test_ui_device_list_run_and_history_are_secret_free(self):
        service = FakeProphylaxis()
        client = self.client(service)

        devices = client.get("/api/prophylaxis/devices")
        result = client.post("/api/prophylaxis/devices/10/checks/cpu")
        history = client.get("/api/prophylaxis/results?limit=20")

        self.assertEqual(devices.status_code, 200)
        self.assertEqual(devices.json()[0]["name"], "SW01")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["values"]["current_percent"], 8.0)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(service.checked, [10])
        serialized = devices.text + result.text + history.text
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("username", serialized.lower())

    def test_report_history_requires_operator_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            token_file = Path(temporary) / "operator-token"
            token_file.write_text("operator-token-for-prophylaxis-tests-1234", encoding="utf-8")
            response = self.client(
                FakeProphylaxis(),
                app_settings=Settings(
                    environment="test", log_level="CRITICAL", _env_file=None,
                    monitoring_operator_token_file=token_file,
                ),
            ).get("/api/prophylaxis/runs")
            self.assertEqual(401, response.status_code)

    def test_store_failure_uses_safe_error_contract(self):
        client = self.client(FakeProphylaxis(error=ResultStoreError()))

        result = client.post("/api/prophylaxis/devices/10/checks/cpu")
        history = client.get("/api/prophylaxis/results")

        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.json()["error"]["code"], "result_store_unavailable")
        self.assertEqual(history.status_code, 500)

    def test_report_summary_counts_statuses_without_serializing_raw_cli(self):
        payload = {
            "schema_version": "1.0", "run_id": "run-1",
            "tenant": {"name": "Customer A"}, "site": {"name": "DC A"},
            "started_at": "2026-11-12T10:00:00Z",
            "devices": [{
                "device": {"netbox_id": 7, "hostname": "C9500-01", "platform": "cisco-ios-xe"},
                "collection": {"status": "partial", "attempted": 3, "successful": 2, "unsupported": 1},
                "outputs": {
                    "show version": {"parsing_status": "success", "stdout": "secret raw output"},
                    "show logging": {"parsing_status": "parse_error", "stdout": "raw log output"},
                },
                "ignored_commands": [{"command": "show redundancy", "status": "unsupported"}],
            }],
        }
        summary = _report_summary(payload)
        rendered = str(summary)
        self.assertEqual(3, summary["commands_attempted"])
        self.assertEqual(1, summary["devices_partial"])
        self.assertEqual(1, summary["parsing_successful"])
        self.assertEqual(1, summary["parsing_failed"])
        self.assertNotIn("secret raw output", rendered)
        self.assertNotIn("raw log output", rendered)


if __name__ == "__main__":
    unittest.main()
