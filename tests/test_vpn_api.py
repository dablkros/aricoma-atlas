import unittest
from datetime import datetime, timezone
import tempfile
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.vpn.models import VPNConnectionPage, VPNConnectionRecord, VPNConnectivityTestResult
from atlas.vpn.service import VPNRegistryError


OPERATOR_TOKEN = "vpn-operator-token-for-tests-1234567890"


def record() -> VPNConnectionRecord:
    now = datetime.now(timezone.utc)
    return VPNConnectionRecord(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_id="customer-a-primary",
        name="Customer A primary",
        tenant_id=1,
        tenant_slug="customer-a",
        vendor="fortinet",
        protocol="ssl-vpn",
        driver="forticlient",
        gateway_host="vpn.example.test",
        gateway_port=443,
        auth_method="username_password",
        secret_ref="vpn/connections/customer-a-primary",
        management_vrf_id=30,
        allowed_prefixes=["192.168.1.0/24"],
        administrative_status="planned",
        created_at=now,
        updated_at=now,
    )


class FakeVPNService:
    def __init__(self):
        self.created = None

    def list(self, *, tenant_id=None, limit=50, offset=0):
        item = record()
        if tenant_id not in {None, item.tenant_id}:
            return VPNConnectionPage(items=[], total=0, limit=limit, offset=offset)
        return VPNConnectionPage(items=[item], total=1, limit=limit, offset=offset)

    def create(self, value):
        self.created = value
        return record()

    def get(self, connection_id):
        if connection_id != "customer-a-primary":
            raise VPNRegistryError("connection_not_found", 404)
        return record()

    def patch(self, connection_id, changes):
        return self.get(connection_id)

    def deactivate(self, connection_id):
        value = self.get(connection_id)
        return value.model_copy(update={"administrative_status": "disabled"})

    def validate(self, connection_id):
        self.get(connection_id)
        return {
            "connection_id": connection_id,
            "secret_available": True,
            "runtime_status": "not_deployed",
            "validation_status": "valid",
        }

    def resolve_device(self, device_id, *, expected_tenant=None):
        return {
            "device_id": device_id,
            "device_name": "A-SW01",
            "tenant_id": expected_tenant or 1,
            "tenant_slug": "customer-a",
            "site_id": 20,
            "management_ip": "192.168.1.10",
            "vrf_id": 30,
            "connection_id": "customer-a-primary",
            "connection_source": "site",
            "administrative_status": "planned",
            "runtime_status": "not_deployed",
            "resolution_status": "resolved",
            "vendor": "fortinet",
            "protocol": "ssl-vpn",
            "driver": "forticlient",
            "gateway_host": "vpn.example.test",
            "gateway_port": 443,
        }

    def assignment_options(self, tenant_id):
        return {
            "tenant": {"id": tenant_id, "name": "Customer A", "slug": "customer-a"},
            "connections": [{
                "connection_id": "customer-a-primary",
                "name": "Customer A primary",
                "administrative_status": "planned",
                "management_vrf_id": 30,
            }],
            "sites": [{
                "id": 20,
                "name": "Customer A DC",
                "slug": "customer-a-dc",
                "connection_id": None,
            }],
            "devices": [],
        }

    def plan_assignment(
        self, *, tenant_id, connection_id, target_type, target_ids,
    ):
        return {
            "tenant": {"id": tenant_id, "name": "Customer A", "slug": "customer-a"},
            "connection": {
                "connection_id": connection_id,
                "name": "Customer A primary",
                "management_vrf_id": 30,
            } if connection_id else None,
            "target_type": target_type,
            "operations": [{
                "target_type": target_type,
                "target_id": target_ids[0],
                "target_name": "Customer A DC",
                "current_connection_id": None,
                "desired_connection_id": connection_id,
                "action": "ASSIGN" if connection_id else "UNCHANGED",
            }],
            "apply_allowed": True,
        }

    def apply_assignment(self, *, expected_plan, **_values):
        return {**expected_plan, "status": "APPLIED"}


class FakeVPNTestService:
    def __init__(self):
        self.calls = []

    def run(self, connection_id, *, device_id, target_port):
        self.calls.append((connection_id, device_id, target_port))
        return VPNConnectivityTestResult(**{
            "connection_id": connection_id,
            "device_id": device_id,
            "device_name": "A-SW01",
            "target_ip": "192.168.1.10",
            "target_port": target_port,
            "status": "passed",
            "tunnel_established": True,
            "target_reachable": True,
            "assigned_address": "100.64.1.2",
            "disconnected": True,
            "duration_ms": 1200,
            "failure_stage": None,
            "message": "The tunnel established and the target answered.",
        })


class VPNApiTests(unittest.TestCase):
    def setUp(self):
        self.token_file = tempfile.NamedTemporaryFile(mode="w+")
        self.token_file.write(OPERATOR_TOKEN)
        self.token_file.flush()
        self.service = FakeVPNService()
        self.vpn_test = FakeVPNTestService()
        dependencies = SimpleNamespace(vpn=self.service, vpn_test=self.vpn_test, openbao=None)
        settings = Settings(
            environment="test",
            log_level="CRITICAL",
            monitoring_operator_token_file=self.token_file.name,
            oxidized_enabled=False,
            zabbix_enabled=False,
            _env_file=None,
        )
        self.client = TestClient(
            create_app(settings, dependencies=dependencies),
            raise_server_exceptions=False,
        )
        self.headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

    def tearDown(self):
        self.token_file.close()

    def test_registry_is_not_exposed_without_authorization(self):
        response = self.client.get("/api/v1/vpn/connections?tenant_id=1")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "vpn_unauthorized")

    def test_connectivity_test_requires_authorization(self):
        response = self.client.post(
            "/api/v1/vpn/connections/customer-a-primary/test",
            json={"device_id": 10, "target_port": 22},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.vpn_test.calls, [])

    def test_connectivity_test_returns_tunnel_and_cleanup_evidence(self):
        response = self.client.post(
            "/api/v1/vpn/connections/customer-a-primary/test",
            json={"device_id": 10, "target_port": 22},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.vpn_test.calls, [("customer-a-primary", 10, 22)])
        self.assertTrue(response.json()["tunnel_established"])
        self.assertTrue(response.json()["target_reachable"])
        self.assertTrue(response.json()["disconnected"])
        self.assertNotIn("password", response.text.lower())

    def test_list_is_paginated_and_secret_values_are_never_serialized(self):
        response = self.client.get(
            "/api/v1/vpn/connections?tenant_id=1&limit=10&offset=0",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["runtime_status"], "not_deployed")
        item = response.json()["items"][0]
        forbidden_secret_fields = {
            "username",
            "password",
            "private_key",
            "psk",
            "api_token",
            "token",
            "secret_value",
        }
        self.assertTrue(forbidden_secret_fields.isdisjoint(item))
        self.assertNotIn("never-return-me", response.text)

    def test_create_rejects_secret_material_before_calling_service(self):
        payload = {
            "connection_id": "customer-a-primary",
            "name": "Customer A primary",
            "tenant_id": 1,
            "vendor": "fortinet",
            "protocol": "ssl-vpn",
            "driver": "forticlient",
            "gateway_host": "vpn.example.test",
            "gateway_port": 443,
            "auth_method": "username_password",
            "secret_ref": "vpn/connections/customer-a-primary",
            "management_vrf_id": 30,
            "allowed_prefixes": ["192.168.1.0/24"],
            "administrative_status": "planned",
            "password": "must-never-cross-the-registry-boundary",
        }

        response = self.client.post(
            "/api/v1/vpn/connections", json=payload, headers=self.headers,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.service.created)
        self.assertNotIn(payload["password"], response.text)

    def test_not_found_uses_stable_error_contract(self):
        response = self.client.get(
            "/api/v1/vpn/connections/unknown", headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "connection_not_found")

    def test_assignment_options_require_authorization(self):
        response = self.client.get("/api/v1/vpn/assignments/options?tenant_id=1")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "vpn_unauthorized")

    def test_assignment_plan_hash_is_required_for_apply(self):
        payload = {
            "tenant_id": 1,
            "connection_id": "customer-a-primary",
            "target_type": "site",
            "target_ids": [20],
        }
        planned = self.client.post(
            "/api/v1/vpn/assignments/plan", json=payload, headers=self.headers,
        )

        self.assertEqual(planned.status_code, 200)
        self.assertRegex(planned.json()["plan_hash"], r"^[a-f0-9]{64}$")

        stale = self.client.post(
            "/api/v1/vpn/assignments/apply", json=payload, headers=self.headers,
        )
        applied = self.client.post(
            "/api/v1/vpn/assignments/apply",
            json={**payload, "plan_hash": planned.json()["plan_hash"]},
            headers=self.headers,
        )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "vpn_assignment_plan_stale")
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()["status"], "APPLIED")


if __name__ == "__main__":
    unittest.main()
