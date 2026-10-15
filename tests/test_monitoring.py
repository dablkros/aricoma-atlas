import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.monitoring.fortigate import FortiGateMonitoringDriver
from atlas.monitoring.models import Device, MonitoringError, device_options, device_reference
from atlas.monitoring.secrets import MonitoringSecrets
from atlas.monitoring.store import JobStore
from atlas.monitoring.service import MonitoringService
from atlas.services.errors import DependencyError


def raw_device(**overrides):
    value = {
        "id": 42,
        "name": "fw-1",
        "status": {"value": "active"},
        "device_type": {"manufacturer": {"slug": "fortinet"}},
        "platform": {"slug": "fortios"},
        "primary_ip4": {"address": "10.0.0.10/24"},
        "site": {"slug": "bratislava"},
        "custom_fields": {"monitoring_enabled": True, "credential_profile": "edge"},
    }
    value.update(overrides)
    return value


class MonitoringModelTests(unittest.TestCase):
    def test_netbox_reference_requires_exact_active_fortios_contract(self):
        device = device_reference(raw_device())
        self.assertEqual((device.id, device.address, device.profile), (42, "10.0.0.10", "edge"))
        for change, code in (
            ({"status": {"value": "offline"}}, "device_not_active"),
            ({"platform": {"slug": "ios"}}, "unsupported_monitoring_driver"),
            ({"custom_fields": {"monitoring_enabled": False}}, "monitoring_disabled"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(MonitoringError, code):
                device_reference(raw_device(**change))

    def test_device_options_are_allowlisted_and_tls_fails_closed(self):
        device = device_reference(raw_device())
        base = {"allowed_device_networks": ["10.0.0.0/24"], "devices": {"42": {
            "interface": "port1", "vdom": "root", "source_ips": ["10.0.0.5"]}}}
        self.assertTrue(device_options(device, base)["tls_verify"])
        base["devices"]["42"]["tls_verify"] = False
        with self.assertRaisesRegex(MonitoringError, "invalid_tls_configuration"):
            device_options(device, base)
        base["devices"]["42"]["lab_tls_exception"] = True
        self.assertFalse(device_options(device, base)["tls_verify"])


class MonitoringSecretTests(unittest.TestCase):
    def test_secret_is_device_specific_generated_once_and_written_with_cas(self):
        service = Mock()
        service.read_secret_with_metadata.side_effect = [
            DependencyError("secret_not_found"),
            ({"version": "2c", "community": "A" * 32, "managed_by": "atlas",
              "device_id": "42", "netbox_instance": "customer-a", "community_id": 7,
              "vdom": "root"}, {"version": 1}),
        ]
        service._authenticated.side_effect = lambda callback: callback("runtime-token")
        secrets = MonitoringSecrets(service)
        device = Device(42, "fw-1", "10.0.0.10", "fortinet", "fortios", "edge", "ba")
        value, version = secrets.ensure(device, "customer-a", 7, {"vdom": "root"})
        self.assertEqual(version, 1)
        self.assertEqual(value["device_id"], "42")
        callback = service._authenticated.call_args.args[0]
        service.client.kv_write.reset_mock()
        callback("runtime-token")
        service.client.kv_write.assert_called_once()
        args, kwargs = service.client.kv_write.call_args
        self.assertEqual(args[:2], ("runtime-token", "devices/snmp/customer-a/fortinet/42/monitoring"))
        self.assertEqual(kwargs["cas"], 0)


class FortiGateDriverTests(unittest.TestCase):
    def setUp(self):
        self.device = Device(42, "fw-1", "10.0.0.10", "fortinet", "fortios", "edge", "ba")
        self.options = {"https_port": 443, "snmp_port": 161, "vdom": "root", "interface": "port1",
                        "source_ips": ["10.0.0.5"], "tls_verify": True}
        self.driver = FortiGateMonitoringDriver(self.device, self.options, "secret-token", requester=Mock())

    def test_plan_preserves_customer_communities_and_detects_owned_conflict(self):
        state = {"sysinfo": {"status": "enable"}, "interface": {"allowaccess": "https snmp"},
                 "communities": [{"id": 2, "name": "customer"}]}
        secret = {"community_id": 7, "community": "AtlasCommunity", "vdom": "root"}
        self.assertEqual(self.driver.plan(state, secret), ["Create Atlas community restricted to monitoring sources"])
        state["communities"].append({"id": 7, "name": "someone-else"})
        with self.assertRaisesRegex(MonitoringError, "community_ownership_conflict"):
            self.driver.plan(state, secret)

    def test_requests_use_bearer_no_redirects_and_tls_verification(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "success", "version": "v7.0.9", "results": {"status": "enable"}}
        self.driver.requester.request.return_value = response
        self.driver.request("GET", "system.snmp/sysinfo")
        kwargs = self.driver.requester.request.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secret-token"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["verify"])


class JobStoreTests(unittest.TestCase):
    def test_plan_expires_and_interrupted_job_is_recovered_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(directory)
            plan_id = store.plan({"device_id": 42, "mode": "monitor_only", "fingerprint": "hash"})
            with store.database() as database:
                plan = json.loads(database.execute("SELECT body FROM plans WHERE id=?", (plan_id,)).fetchone()[0])
                plan["created_at"] = time.time() - 901
                database.execute("UPDATE plans SET body=? WHERE id=?", (json.dumps(plan), plan_id))
            with self.assertRaisesRegex(MonitoringError, "plan_expired"):
                store.get_plan(plan_id)
            job = store.create(42, "monitor_only")
            store.recover()
            recovered = store.get(job["job_id"])
            self.assertEqual(recovered["status"], "PARTIAL")
            self.assertNotIn("community", json.dumps(recovered).lower())


class MonitoringServiceTests(unittest.TestCase):
    def test_monitor_only_never_loads_management_credentials_or_generates_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "monitoring.yaml"
            config.write_text(
                "version: 1\nnetbox_instance: customer-a\n"
                "allowed_device_networks: [10.0.0.0/24]\n"
                "devices:\n  '42':\n    interface: port1\n    vdom: root\n"
                "    source_ips: [10.0.0.5]\n",
                encoding="utf-8",
            )
            netbox = Mock()
            netbox.get.return_value = raw_device()
            openbao = Mock()
            openbao.read_secret_with_metadata.side_effect = DependencyError("secret_not_found")
            service = MonitoringService(netbox, openbao, Mock(), config, root / "jobs")
            with self.assertRaisesRegex(MonitoringError, "snmp_credentials_missing"):
                service.inspect(42, "monitor_only")
            openbao.get_device_credentials.assert_not_called()


class FakeMonitoring:
    def __init__(self):
        self.store = SimpleNamespace(directory=Path("/path/that/does/not/exist"), recover=lambda: None, get_plan=lambda _identifier: {
            "device_id": 42, "mode": "monitor_only"})
        self.runs = []

    def list_devices(self):
        return [{"id": 42, "name": "fw-1"}]

    def submit(self, device_id, mode, plan_id):
        return {"job_id": "11111111-1111-4111-8111-111111111111"}

    def run(self, job_id, plan_id):
        self.runs.append((job_id, plan_id))


class MonitoringApiTests(unittest.TestCase):
    def test_monitoring_routes_require_token_and_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token"
            token_file.write_text("t" * 40, encoding="utf-8")
            monitoring = FakeMonitoring()
            settings = Settings(
                environment="test",
                monitoring_operator_token_file=token_file,
                monitoring_jobs_directory=Path(directory) / "jobs",
            )
            client = TestClient(create_app(settings, SimpleNamespace(monitoring=monitoring)))
            self.assertEqual(client.get("/api/monitoring/devices").status_code, 401)
            headers = {"Authorization": f"Bearer {'t' * 40}"}
            self.assertEqual(client.get("/api/monitoring/devices", headers=headers).status_code, 200)
            rejected = client.post(
                "/api/monitoring/devices/42/reconcile", headers=headers,
                json={"mode": "monitor_only", "plan_id": "22222222-2222-4222-8222-222222222222", "confirmed": False},
            )
            self.assertEqual(rejected.status_code, 422)


if __name__ == "__main__":
    unittest.main()
