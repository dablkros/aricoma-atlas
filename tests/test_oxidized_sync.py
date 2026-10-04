import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas.device_credentials import DeviceValidationError
from atlas.oxidized_inventory import (
    InventoryError,
    inventory_diff,
    read_inventory,
    write_inventory,
)
from atlas.services.errors import DependencyError
from atlas.services.oxidized_sync import (
    FileInventoryStore,
    OxidizedSyncFailure,
    OxidizedSyncService,
    SyncLock,
)


def device(
    name="SW01",
    address="192.0.2.1/24",
    platform="cisco-ios-xe",
    vendor="cisco",
    profile="default",
):
    return {
        "id": 1,
        "name": name,
        "primary_ip4": {"address": address} if address is not None else None,
        "platform": {"slug": platform} if platform is not None else None,
        "device_type": {
            "manufacturer": {"slug": vendor} if vendor is not None else None
        },
        "custom_fields": {
            "oxidized_enabled": True,
            "credential_profile": profile,
        },
    }


def entry(
    name="SW01",
    address="192.0.2.1",
    model="ios",
    username="backup",
    password="fixture-password",
    enable=None,
):
    value = {
        "name": name,
        "ip": address,
        "model": model,
        "username": username,
        "password": password,
    }
    if enable is not None:
        value["enable"] = enable
    return value


class FakeNetBox:
    def __init__(self, devices=None, error=None):
        self.devices = list(devices or [])
        self.error = error

    def get_oxidized_devices(self):
        if self.error:
            raise self.error
        return list(self.devices)


class FakeOpenBao:
    def __init__(self, credentials=None, errors=None):
        self.credentials = credentials or {}
        self.errors = errors or {}
        self.calls = []

    def get_device_credentials(self, vendor, profile):
        self.calls.append((vendor, profile))
        if profile in self.errors:
            raise self.errors[profile]
        return dict(
            self.credentials.get(
                profile,
                {"username": "backup", "password": "fixture-password"},
            )
        )


class MemoryStore:
    def __init__(self, current=None, read_error=None, write_error=None):
        self.current = list(current or [])
        self.read_error = read_error
        self.write_error = write_error
        self.writes = []
        self.pending_reload = False

    def read(self):
        if self.read_error:
            raise self.read_error
        return list(self.current)

    def write(self, inventory):
        if self.write_error:
            raise self.write_error
        self.current = list(inventory)
        self.writes.append(list(inventory))

    def reload_required(self):
        return self.pending_reload

    def mark_reload_required(self):
        self.pending_reload = True

    def clear_reload_required(self):
        self.pending_reload = False


class FakeOxidized:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def reload_inventory(self, inventory):
        self.calls.append(list(inventory))
        if self.error:
            raise self.error


class InventoryDiffTests(unittest.TestCase):
    def test_empty_add_same_remove_and_order_only(self):
        sw1 = entry("SW01")
        sw2 = entry("SW02", "192.0.2.2")

        self.assertEqual(inventory_diff([], [sw1]).added, ("SW01",))
        self.assertEqual(inventory_diff([sw1], [sw1]).unchanged, ("SW01",))
        self.assertEqual(inventory_diff([sw1], []).removed, ("SW01",))
        ordered = inventory_diff([sw2, sw1], [sw1, sw2])
        self.assertEqual(ordered.unchanged, ("SW01", "SW02"))
        self.assertFalse(ordered.changed)

    def test_runtime_field_changes_are_updated_without_exposing_values(self):
        original = entry()
        changes = {
            "model": "junos",
            "ip": "2001:db8::1",
            "username": "new-user",
            "password": "new-password",
            "enable": "new-enable",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = dict(original)
                changed[field] = value
                diff = inventory_diff([original], [changed])
                self.assertEqual(diff.updated, ("SW01",))
                self.assertNotIn("new-password", repr(diff))
                self.assertNotIn("new-enable", repr(diff))


class InventoryFileTests(unittest.TestCase):
    def test_missing_empty_valid_and_invalid_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "router.json"
            self.assertEqual(read_inventory(path).state, "missing")

            path.write_text("", encoding="utf-8")
            with self.assertRaises(InventoryError) as raised:
                read_inventory(path)
            self.assertEqual(raised.exception.code, "empty_file")

            path.write_text("[]\n", encoding="utf-8")
            self.assertEqual(read_inventory(path).state, "empty")

            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(InventoryError) as raised:
                read_inventory(path)
            self.assertEqual(raised.exception.code, "invalid_json")

            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(InventoryError) as raised:
                read_inventory(path)
            self.assertEqual(raised.exception.code, "wrong_schema")

    def test_atomic_replace_permissions_and_failure_preserves_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "router.json"
            original = [entry()]
            write_inventory(path, original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

            with patch("atlas.deployment.os.replace", side_effect=OSError("fail")):
                with self.assertRaises(InventoryError) as raised:
                    write_inventory(path, [])
            self.assertEqual(raised.exception.code, "write_failed")
            self.assertEqual(list(read_inventory(path).entries), original)

    def test_reload_marker_is_private_and_persists_until_cleared(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileInventoryStore(Path(temporary) / "router.json")

            self.assertFalse(store.reload_required())
            store.mark_reload_required()
            self.assertTrue(store.reload_required())
            self.assertEqual(store.reload_marker.stat().st_mode & 0o777, 0o640)
            store.clear_reload_required()
            self.assertFalse(store.reload_required())


class OxidizedSyncTests(unittest.TestCase):
    def service(self, devices, current=None, bao=None, oxidized=None, store=None):
        store = store or MemoryStore(current)
        oxidized = oxidized or FakeOxidized()
        return (
            OxidizedSyncService(
                FakeNetBox(devices),
                bao or FakeOpenBao(),
                oxidized,
                store,
            ),
            store,
            oxidized,
        )

    def test_successful_add_and_idempotent_second_sync(self):
        service, store, oxidized = self.service([device()])

        first = service.sync()
        second = service.sync()

        self.assertEqual(first.status, "success")
        self.assertEqual(first.added, ("SW01",))
        self.assertTrue(first.inventory_updated)
        self.assertTrue(first.oxidized_reloaded)
        self.assertEqual(second.summary.unchanged, 1)
        self.assertFalse(second.inventory_changed)
        self.assertFalse(second.inventory_updated)
        self.assertFalse(second.oxidized_reloaded)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(len(oxidized.calls), 1)

    def test_explicit_disable_and_deleted_device_are_removed(self):
        for scenario in ("disabled", "deleted"):
            with self.subTest(scenario=scenario):
                service, store, _oxidized = self.service([], [entry()])
                result = service.sync()
                self.assertEqual(result.removed, ("SW01",))
                self.assertEqual(store.current, [])

    def test_credential_failure_preserves_existing_device(self):
        missing = DeviceValidationError(
            "missing",
            code="credential_not_found",
            category="error",
        )
        service, store, oxidized = self.service(
            [device(profile="missing")],
            [entry()],
            bao=FakeOpenBao(errors={"missing": missing}),
        )

        result = service.sync()

        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.summary.errors, 1)
        self.assertEqual(result.issues[0].code, "credential_not_found")
        self.assertEqual(store.current, [entry()])
        self.assertEqual(store.writes, [])
        self.assertEqual(oxidized.calls, [])

    def test_structured_lifecycle_logs_do_not_contain_credentials(self):
        bao = FakeOpenBao(
            credentials={
                "default": {
                    "username": "sensitive-user",
                    "password": "sensitive-password",
                    "enable_password": "sensitive-enable",
                }
            }
        )
        service, _store, _oxidized = self.service([device()], bao=bao)

        with self.assertLogs("atlas.oxidized.sync", level=logging.INFO) as logs:
            service.sync()

        rendered = "\n".join(logs.output)
        for secret in (
            "sensitive-user",
            "sensitive-password",
            "sensitive-enable",
        ):
            self.assertNotIn(secret, rendered)

    def test_invalid_platform_preserves_existing_device(self):
        service, store, _oxidized = self.service(
            [device(platform="unsupported")],
            [entry()],
        )

        result = service.sync()

        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.summary.skipped, 1)
        self.assertEqual(result.issues[0].code, "unsupported_platform")
        self.assertEqual(store.current, [entry()])

    def test_inconsistent_enabled_payload_preserves_existing_device(self):
        inconsistent = device()
        inconsistent["custom_fields"] = {}
        service, store, _oxidized = self.service([inconsistent], [entry()])

        result = service.sync()

        self.assertEqual(result.issues[0].code, "invalid_oxidized_enabled")
        self.assertEqual(store.current, [entry()])
        self.assertFalse(result.inventory_changed)

    def test_unnamed_invalid_device_suppresses_unrelated_removals(self):
        unnamed = device(name=None)
        service, store, _oxidized = self.service([unnamed], [entry()])

        result = service.sync()

        self.assertEqual(result.issues[0].code, "missing_name")
        self.assertEqual(store.current, [entry()])
        self.assertFalse(result.inventory_changed)

    def test_global_dependency_failure_never_writes_partial_inventory(self):
        cases = (
            (
                FakeNetBox(error=DependencyError("connection_failed")),
                FakeOpenBao(),
                "netbox_unavailable",
            ),
            (
                FakeNetBox([device(profile="global")]),
                FakeOpenBao(errors={"global": DependencyError("connection_failed")}),
                "openbao_unavailable",
            ),
        )
        for netbox, bao, code in cases:
            with self.subTest(code=code):
                store = MemoryStore([entry()])
                service = OxidizedSyncService(
                    netbox,
                    bao,
                    FakeOxidized(),
                    store,
                )
                with self.assertRaises(OxidizedSyncFailure) as raised:
                    service.sync()
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(store.writes, [])

    def test_invalid_current_and_write_failure_are_global(self):
        cases = (
            (MemoryStore(read_error=InventoryError("invalid_json")), "inventory_invalid"),
            (MemoryStore(write_error=InventoryError("write_failed")), "inventory_write_failed"),
        )
        for store, code in cases:
            with self.subTest(code=code):
                service = OxidizedSyncService(
                    FakeNetBox([device()]),
                    FakeOpenBao(),
                    FakeOxidized(),
                    store,
                )
                with self.assertRaises(OxidizedSyncFailure) as raised:
                    service.sync()
                self.assertEqual(raised.exception.code, code)

    def test_reload_failure_reports_written_but_not_reloaded(self):
        oxidized = FakeOxidized(DependencyError("connection_failed"))
        service, store, _oxidized = self.service(
            [device()],
            oxidized=oxidized,
        )

        result = service.sync()

        self.assertEqual(result.status, "error")
        self.assertTrue(result.inventory_changed)
        self.assertTrue(result.inventory_updated)
        self.assertFalse(result.oxidized_reloaded)
        self.assertEqual(result.issues[-1].code, "oxidized_reload_failed")
        self.assertEqual(len(store.writes), 1)
        self.assertTrue(store.pending_reload)

        oxidized.error = None
        retry = service.sync()

        self.assertFalse(retry.inventory_changed)
        self.assertFalse(retry.inventory_updated)
        self.assertTrue(retry.oxidized_reloaded)
        self.assertFalse(store.pending_reload)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(len(oxidized.calls), 2)

    def test_lock_conflict_returns_409_failure(self):
        lock = SyncLock()
        self.assertTrue(lock.acquire())
        service = OxidizedSyncService(
            FakeNetBox([]),
            FakeOpenBao(),
            FakeOxidized(),
            MemoryStore(),
            sync_lock=lock,
        )
        try:
            with self.assertRaises(OxidizedSyncFailure) as raised:
                service.sync()
            self.assertEqual(raised.exception.code, "sync_already_running")
            self.assertEqual(raised.exception.status_code, 409)
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
