import unittest

from atlas.oxidized_inventory import InventoryError
from atlas.services.errors import DependencyError
from atlas.services.oxidized_operations import (
    OxidizedOperationFailure,
    OxidizedOperationsService,
)


class FakeNetBox:
    def __init__(self, devices=None, failure=None):
        self.devices = devices or []
        self.failure = failure

    def get_oxidized_devices(self):
        if self.failure:
            raise self.failure
        return self.devices


class FakeInventoryStore:
    def __init__(self, entries=None, failure=None):
        self.entries = entries or []
        self.failure = failure

    def read(self):
        if self.failure:
            raise self.failure
        return self.entries


class FakeOxidized:
    def __init__(self, failure=None):
        self.failure = failure
        self.queued = []

    def queue_backup(self, device):
        if self.failure:
            raise self.failure
        self.queued.append(device)


def runtime_device(name="SW01"):
    return {
        "name": name,
        "ip": "192.0.2.10",
        "model": "ios",
        "username": "operator",
        "password": "never-return-this",
        "enable": "never-return-this-either",
    }


class OxidizedOperationsServiceTests(unittest.TestCase):
    def service(self, enabled=None, runtime=None, oxidized=None):
        return OxidizedOperationsService(
            FakeNetBox(enabled),
            oxidized or FakeOxidized(),
            FakeInventoryStore(runtime),
        )

    def test_device_list_contains_only_public_fields(self):
        result = self.service(runtime=[runtime_device()]).list_devices()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "SW01")
        self.assertEqual(result[0].ip, "192.0.2.10")
        self.assertEqual(result[0].model, "ios")
        self.assertNotIn("never-return-this", repr(result))

    def test_status_reports_name_drift_without_resolving_secrets(self):
        result = self.service(
            enabled=[{"name": "SW01"}, {"name": "SW02"}],
            runtime=[runtime_device("SW01"), runtime_device("SW03")],
        ).status()

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.netbox_enabled_devices, 2)
        self.assertEqual(result.runtime_inventory_devices, 2)
        self.assertEqual(result.inventory_issues, 2)

    def test_status_is_healthy_when_names_match(self):
        result = self.service(
            enabled=[{"name": "SW01"}],
            runtime=[runtime_device("SW01")],
        ).status()

        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.inventory_issues, 0)

    def test_valid_runtime_device_is_queued(self):
        oxidized = FakeOxidized()
        selected = self.service(
            runtime=[runtime_device("SW:01")],
            oxidized=oxidized,
        ).queue_backup("SW:01")

        self.assertEqual(selected.name, "SW:01")
        self.assertEqual(oxidized.queued, ["SW:01"])

    def test_invalid_or_unknown_device_is_rejected_before_queue(self):
        oxidized = FakeOxidized()
        service = self.service(runtime=[runtime_device()], oxidized=oxidized)

        cases = (
            ("../SW01", "invalid_device_name", 422),
            ("unknown", "device_not_found", 404),
        )
        for device, code, status_code in cases:
            with self.subTest(device=device):
                with self.assertRaises(OxidizedOperationFailure) as raised:
                    service.queue_backup(device)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.status_code, status_code)
        self.assertEqual(oxidized.queued, [])

    def test_inventory_and_dependency_failures_are_safe(self):
        invalid_inventory = OxidizedOperationsService(
            FakeNetBox(),
            FakeOxidized(),
            FakeInventoryStore(failure=InventoryError("invalid_json")),
        )
        with self.assertRaises(OxidizedOperationFailure) as inventory_failure:
            invalid_inventory.list_devices()
        self.assertEqual(inventory_failure.exception.code, "inventory_invalid")

        unavailable = self.service(
            runtime=[runtime_device()],
            oxidized=FakeOxidized(DependencyError("connection_failed")),
        )
        with self.assertRaises(OxidizedOperationFailure) as dependency_failure:
            unavailable.queue_backup("SW01")
        self.assertEqual(dependency_failure.exception.code, "oxidized_unavailable")


if __name__ == "__main__":
    unittest.main()
