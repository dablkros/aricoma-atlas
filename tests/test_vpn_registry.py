import copy
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from atlas.services.errors import DependencyError
from atlas.vpn.models import VPNConnectionCreate, VPNConnectionPatch
from atlas.vpn.service import VPNRegistryError, VPNRegistryService
from atlas.vpn.store import SQLiteVPNConnectionStore


class FakeNetBox:
    def __init__(self):
        self.objects = {
            "/api/tenancy/tenants/": [
                {"id": 1, "name": "Customer A", "slug": "customer-a"},
                {"id": 2, "name": "Customer B", "slug": "customer-b"},
            ],
            "/api/ipam/vrfs/": [
                {"id": 30, "name": "customer-a-mgmt", "tenant": {"id": 1}},
                {"id": 31, "name": "customer-b-mgmt", "tenant": {"id": 2}},
                {"id": 32, "name": "customer-a-isolated", "tenant": {"id": 1}},
            ],
            "/api/dcim/sites/": [
                {"id": 20, "slug": "customer-a-dc", "tenant": {"id": 1},
                 "custom_fields": {"atlas_connection_id": "customer-a-primary"}},
                {"id": 21, "slug": "customer-b-dc", "tenant": {"id": 2},
                 "custom_fields": {"atlas_connection_id": "customer-b-primary"}},
            ],
            "/api/ipam/ip-addresses/": [
                {"id": 201, "address": "192.168.1.10/24", "tenant": {"id": 1},
                 "vrf": {"id": 30}},
                {"id": 202, "address": "192.168.1.10/24", "tenant": {"id": 2},
                 "vrf": {"id": 31}},
            ],
            "/api/dcim/devices/": [
                {"id": 101, "name": "A-SW01", "tenant": {"id": 1},
                 "site": {"id": 20}, "primary_ip4": {"id": 201},
                 "custom_fields": {}},
                {"id": 102, "name": "B-SW01", "tenant": {"id": 2},
                 "site": {"id": 21}, "primary_ip4": {"id": 202},
                 "custom_fields": {}},
            ],
        }

    @staticmethod
    def _id(value):
        return value.get("id") if isinstance(value, dict) else value

    def get_all(self, endpoint, params=None):
        params = params or {}
        result = []
        for item in self.objects[endpoint]:
            if all(self._id(item.get(key)) == expected for key, expected in params.items()):
                result.append(copy.deepcopy(item))
        return result


class FakeOpenBao:
    def __init__(self):
        self.secrets = {
            "vpn/connections/customer-a-primary": {"password": "never-return-me"},
            "vpn/connections/customer-b-primary": {"psk": "never-return-me-either"},
        }
        self.failure = None

    def read_secret_metadata(self, path):
        if self.failure:
            raise DependencyError(self.failure)
        if path not in self.secrets:
            raise DependencyError("secret_not_found")
        return {
            "current_version": 1,
            "versions": {"1": {"destroyed": False, "deletion_time": ""}},
        }


class VPNRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.netbox = FakeNetBox()
        self.openbao = FakeOpenBao()
        self.store = SQLiteVPNConnectionStore(
            Path(self.temporary.name) / "vpn/connections.sqlite3"
        )
        self.service = VPNRegistryService(self.store, self.netbox, self.openbao)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def connection(
        connection_id="customer-a-primary",
        tenant_id=1,
        vrf_id=30,
        prefixes=None,
    ):
        return VPNConnectionCreate(
            connection_id=connection_id,
            name=f"Connection {connection_id}",
            tenant_id=tenant_id,
            vendor="fortinet",
            protocol="ssl-vpn",
            driver="forticlient",
            gateway_host="vpn.example.test",
            gateway_port=443,
            auth_method="username_password",
            secret_ref=f"vpn/connections/{connection_id}",
            management_vrf_id=vrf_id,
            allowed_prefixes=prefixes or ["192.168.1.0/24"],
            administrative_status="planned",
        )

    def seed(self):
        first = self.service.create(self.connection())
        second = self.service.create(
            self.connection("customer-b-primary", tenant_id=2, vrf_id=31)
        )
        return first, second

    def test_connection_crud_persistence_uniqueness_and_deactivation(self):
        created = self.service.create(self.connection())
        reloaded_service = VPNRegistryService(
            SQLiteVPNConnectionStore(self.store.path), self.netbox, self.openbao,
        )
        loaded = reloaded_service.get(created.connection_id)
        updated = self.service.patch(
            created.connection_id,
            VPNConnectionPatch(name="Customer A primary VPN", administrative_status="enabled"),
        )
        disabled = self.service.deactivate(created.connection_id)

        self.assertEqual(created.id, loaded.id)
        self.assertEqual(updated.name, "Customer A primary VPN")
        self.assertEqual(disabled.administrative_status, "disabled")
        self.assertEqual(self.service.list(tenant_id=1).total, 1)
        with self.assertRaisesRegex(VPNRegistryError, "connection_id_conflict"):
            self.service.create(self.connection())
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.store.path.parent).st_mode & 0o777, 0o700)
        with sqlite3.connect(self.store.path) as database:
            versions = database.execute(
                "SELECT version FROM vpn_registry_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(versions, [(1,)])

    def test_resolver_isolates_tenants_with_overlapping_ip(self):
        self.seed()

        customer_a = self.service.resolve_device(101)
        customer_b = self.service.resolve_device(102)

        self.assertEqual(customer_a["management_ip"], customer_b["management_ip"])
        self.assertEqual(customer_a["connection_id"], "customer-a-primary")
        self.assertEqual(customer_b["connection_id"], "customer-b-primary")
        self.assertNotEqual(customer_a["vrf_id"], customer_b["vrf_id"])

    def test_device_override_has_priority(self):
        self.seed()
        self.service.create(
            self.connection("customer-a-secondary", prefixes=["192.168.1.0/24"])
        )
        self.netbox.objects["/api/dcim/devices/"][0]["custom_fields"] = {
            "atlas_connection_id": "customer-a-secondary"
        }

        result = self.service.resolve_device(101)

        self.assertEqual(result["connection_id"], "customer-a-secondary")
        self.assertEqual(result["connection_source"], "device")

    def test_cross_tenant_mapping_is_rejected(self):
        self.seed()
        self.netbox.objects["/api/dcim/sites/"][0]["custom_fields"] = {
            "atlas_connection_id": "customer-b-primary"
        }

        with self.assertRaisesRegex(VPNRegistryError, "connection_tenant_mismatch"):
            self.service.resolve_device(101)

    def test_missing_mapping_is_reported_without_fallback(self):
        self.netbox.objects["/api/dcim/sites/"][0]["custom_fields"] = {}

        result = self.service.resolve_device(101)

        self.assertEqual(result["resolution_status"], "unmapped")
        self.assertIsNone(result["connection_id"])

    def test_explicit_unknown_mapping_is_not_treated_as_unmapped(self):
        self.netbox.objects["/api/dcim/sites/"][0]["custom_fields"] = {
            "atlas_connection_id": "not-registered"
        }

        with self.assertRaisesRegex(VPNRegistryError, "connection_not_found"):
            self.service.resolve_device(101)

    def test_incorrect_vrf_is_rejected(self):
        self.service.create(
            self.connection("customer-a-primary", vrf_id=32)
        )

        with self.assertRaisesRegex(VPNRegistryError, "connection_vrf_mismatch"):
            self.service.resolve_device(101)

    def test_management_ip_must_match_allowed_prefix(self):
        self.service.create(
            self.connection(prefixes=["10.0.0.0/8"])
        )

        with self.assertRaisesRegex(
            VPNRegistryError, "management_ip_outside_allowed_prefixes"
        ):
            self.service.resolve_device(101)

    def test_validate_handles_openbao_unavailability_without_losing_record(self):
        created = self.service.create(self.connection())
        self.openbao.failure = "connection_failed"

        with self.assertRaisesRegex(
            VPNRegistryError, "vpn_secret_validation_unavailable"
        ):
            self.service.validate(created.connection_id)

        self.assertEqual(self.service.get(created.connection_id).id, created.id)

    def test_validate_rejects_deleted_current_secret_version(self):
        self.service.create(self.connection())
        self.openbao.read_secret_metadata = lambda _path: {
            "current_version": 1,
            "versions": {
                "1": {
                    "destroyed": False,
                    "deletion_time": "2026-10-29T10:00:00Z",
                }
            },
        }

        with self.assertRaisesRegex(VPNRegistryError, "vpn_secret_not_found"):
            self.service.validate("customer-a-primary")

    def test_secret_values_are_not_accepted_or_serialized(self):
        payload = self.connection().model_dump()
        payload["password"] = "must-not-be-stored"

        with self.assertRaises(ValidationError):
            VPNConnectionCreate.model_validate(payload)

        serialized = self.service.create(self.connection()).model_dump_json()
        self.assertNotIn("never-return-me", serialized)
        self.assertIn("vpn/connections/customer-a-primary", serialized)

    def test_secret_reference_is_bound_to_connection_id(self):
        payload = self.connection().model_dump()
        payload["secret_ref"] = "vpn/connections/another-customer"

        with self.assertRaises(ValidationError):
            VPNConnectionCreate.model_validate(payload)

    def test_service_rejects_invalid_cli_pagination(self):
        for values in (
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
            {"tenant_id": 0},
        ):
            with self.subTest(values=values), self.assertRaisesRegex(
                VPNRegistryError, "pagination_invalid"
            ):
                self.service.list(**values)
