from datetime import datetime, timezone
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas.api.main import create_app
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
    def client(self, service):
        dependencies = SimpleNamespace(prophylaxis=service)
        return TestClient(
            create_app(settings(), dependencies=dependencies),
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

    def test_store_failure_uses_safe_error_contract(self):
        client = self.client(FakeProphylaxis(error=ResultStoreError()))

        result = client.post("/api/prophylaxis/devices/10/checks/cpu")
        history = client.get("/api/prophylaxis/results")

        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.json()["error"]["code"], "result_store_unavailable")
        self.assertEqual(history.status_code, 500)


if __name__ == "__main__":
    unittest.main()
