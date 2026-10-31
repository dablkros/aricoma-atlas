import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from atlas.vpn.models import VPNConnectionRecord
from atlas.vpn.worker_client import VPNConnectivityTestService, VPNWorkerClient, VPNWorkerError


def connection(**changes):
    values = dict(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        connection_id="customer-vpn", name="Customer VPN", tenant_id=1,
        tenant_slug="customer", vendor="fortinet", protocol="ipsec-ikev2",
        driver="strongswan", gateway_host="vpn.example.test", gateway_port=500,
        auth_method="psk", secret_ref="vpn/connections/customer-vpn",
        management_vrf_id=5, allowed_prefixes=[], administrative_status="planned",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    values.update(changes)
    return VPNConnectionRecord(**values)


class Registry:
    def __init__(self, record=None, resolved_connection="customer-vpn"):
        self.record = record or connection()
        self.resolved_connection = resolved_connection

    def get(self, connection_id):
        return self.record

    def resolve_device(self, device_id, *, expected_tenant=None):
        return {
            "device_id": device_id, "device_name": "switch-1", "tenant_id": 1,
            "management_ip": "10.0.0.10", "resolution_status": "resolved",
            "connection_id": self.resolved_connection,
        }


class Worker:
    def __init__(self):
        self.payload = None

    def test(self, payload):
        self.payload = payload
        return {
            "status": "passed", "tunnel_established": True,
            "target_reachable": True, "assigned_address": "100.64.0.2",
            "disconnected": True, "duration_ms": 42, "failure_stage": None,
            "message": "ok",
        }


class VPNConnectivityServiceTests(unittest.TestCase):
    def test_requires_device_to_resolve_to_exact_connection(self):
        service = VPNConnectivityTestService(Registry(resolved_connection="other-vpn"), Worker())
        with self.assertRaisesRegex(VPNWorkerError, "vpn_test_target_not_assigned"):
            service.run("customer-vpn", device_id=7, target_port=22)

    def test_sends_only_validated_metadata_and_returns_sanitized_result(self):
        worker = Worker()
        service = VPNConnectivityTestService(Registry(), worker)
        result = service.run("customer-vpn", device_id=7, target_port=443)

        self.assertEqual(worker.payload["target_ip"], "10.0.0.10")
        self.assertEqual(worker.payload["secret_ref"], "vpn/connections/customer-vpn")
        self.assertNotIn("password", worker.payload)
        self.assertEqual(result.status, "passed")
        self.assertTrue(result.disconnected)

    def test_supports_classic_ssl_vpn_driver(self):
        worker = Worker()
        record = connection(protocol="ssl-vpn", driver="openfortivpn",
                            gateway_port=443, auth_method="username_password")
        result = VPNConnectivityTestService(Registry(record=record), worker).run(
            "customer-vpn", device_id=7, target_port=443,
        )
        self.assertEqual((worker.payload["driver"], worker.payload["protocol"]),
                         ("openfortivpn", "ssl-vpn"))
        self.assertEqual(result.status, "passed")


class VPNWorkerClientTests(unittest.TestCase):
    def test_rejects_invalid_local_token_without_network_call(self):
        with tempfile.NamedTemporaryFile(mode="w+") as token:
            token.write("short")
            token.flush()
            client = VPNWorkerClient("http://worker", token.name)
            with patch("atlas.vpn.worker_client.requests.post") as post:
                with self.assertRaisesRegex(VPNWorkerError, "vpn_worker_unavailable"):
                    client.test({})
                post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
