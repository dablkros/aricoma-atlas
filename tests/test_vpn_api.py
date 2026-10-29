import unittest
from datetime import datetime, timezone
import tempfile
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.vpn.models import VPNConnectionPage, VPNConnectionRecord
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


class VPNApiTests(unittest.TestCase):
    def setUp(self):
        self.token_file = tempfile.NamedTemporaryFile(mode="w+")
        self.token_file.write(OPERATOR_TOKEN)
        self.token_file.flush()
        self.service = FakeVPNService()
        dependencies = SimpleNamespace(vpn=self.service, openbao=None)
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

    def test_list_is_paginated_and_secret_values_are_never_serialized(self):
        response = self.client.get(
            "/api/v1/vpn/connections?tenant_id=1&limit=10&offset=0",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["runtime_status"], "not_deployed")
        rendered = response.text.lower()
        self.assertNotIn("password", rendered)
        self.assertNotIn("private_key", rendered)
        self.assertNotIn("psk", rendered)

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


if __name__ == "__main__":
    unittest.main()
