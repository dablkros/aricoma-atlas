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
from atlas.monitoring.models import Device, MonitoringError, device_reference, load_config, site_options
from atlas.monitoring.network import AnsibleNetworkMonitoringDriver
from atlas.monitoring.secrets import MonitoringSecrets
from atlas.monitoring.store import JobStore
from atlas.monitoring.service import MonitoringService
from atlas.monitoring.zabbix import KEY, OID, MonitoringZabbix
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry
from atlas.services.errors import DependencyError
from atlas.services.device_resolution import (
    DeviceResolutionError,
    resolve_primary_interface,
    resolve_tcp_service_port,
)


ROOT = Path(__file__).resolve().parent.parent


def raw_device(**overrides):
    value = {
        "id": 42,
        "name": "fw-1",
        "status": {"value": "active"},
        "device_type": {"manufacturer": {"slug": "fortinet"}},
        "platform": {"slug": "fortios"},
        "primary_ip4": {"id": 99, "address": "10.0.0.10/24"},
        "site": {"slug": "bratislava"},
        "custom_fields": {"monitoring_enabled": True, "credential_profile": "edge"},
    }
    value.update(overrides)
    return value


class MonitoringModelTests(unittest.TestCase):
    def test_deployment_policies_cover_all_private_ipv4_ranges(self):
        expected = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
        for name in ("monitoring.default.yaml", "monitoring.example.yaml"):
            with self.subTest(name=name):
                config = load_config(ROOT / "deployment" / name)
                self.assertEqual(config["allowed_device_networks"], expected)

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

    def test_netbox_reference_accepts_only_explicit_vendor_platform_pairs(self):
        pairs = (
            ("fortinet", "fortios"),
            ("cisco", "cisco-ios"),
            ("cisco", "cisco-ios-xe"),
            ("cisco", "cisco-cbs"),
            ("cisco", "cisco-cbs250-350"),
            ("hpe", "aruba-aos-cx"),
            ("hpe", "aruba-aos-switch"),
        )
        for vendor, platform in pairs:
            with self.subTest(vendor=vendor, platform=platform):
                device = device_reference(
                    raw_device(
                        device_type={"manufacturer": {"slug": vendor}},
                        platform={"slug": platform},
                    )
                )
                self.assertEqual((device.vendor, device.platform), (vendor, platform))
        with self.assertRaisesRegex(MonitoringError, "unsupported_monitoring_driver"):
            device_reference(
                raw_device(
                    device_type={"manufacturer": {"slug": "hpe"}},
                    platform={"slug": "cisco-ios"},
                )
            )

    def test_site_policy_has_no_device_inventory_and_is_allowlisted(self):
        device = device_reference(raw_device())
        config = {"allowed_device_networks": ["10.0.0.0/24"],
            "fortios": {"default_snmp_port": 161, "default_vdom": "root", "tls_verify": True},
            "monitoring": {"source_ip": "10.0.0.5"}}
        options = site_options(device, config, https_port=444, interface="port1")
        self.assertEqual((options["https_port"], options["interface"]), (444, "port1"))
        with self.assertRaisesRegex(MonitoringError, "device_network_not_allowed"):
            site_options(device, {**config, "allowed_device_networks": ["192.0.2.0/24"]},
                         https_port=443, interface="port1")
        with self.assertRaisesRegex(MonitoringError, "monitoring_source_ip_unresolved"):
            site_options(device, {**config, "monitoring": {}}, https_port=443, interface="port1")

    def test_v1_config_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring.yaml"
            path.write_text("version: 1\nnetbox_instance: legacy\ndevices: {}\n"
                            "allowed_device_networks: [10.0.0.0/8]\n")
            with self.assertRaisesRegex(MonitoringError, "monitoring_configuration_v1_migration_required"):
                load_config(path)

    def test_shared_service_and_primary_interface_resolution(self):
        self.assertEqual(resolve_tcp_service_port([], 443), 443)
        self.assertEqual(resolve_tcp_service_port([{"port_mappings": ["tcp/444"]}], 443), 444)
        with self.assertRaisesRegex(DeviceResolutionError, "invalid_service_port"):
            resolve_tcp_service_port([{"port_mappings": ["udp/444"]}], 443)
        with self.assertRaisesRegex(DeviceResolutionError, "ambiguous_service_port"):
            resolve_tcp_service_port([{"port_mappings": ["tcp/443", "tcp/444"]}], 443)
        netbox = Mock()
        netbox.get.side_effect = [
            {"address": "10.0.0.10/24", "assigned_object_type": "dcim.interface",
             "assigned_object_id": 7, "assigned_object": {"id": 7}},
            {"id": 7, "name": "port1", "device": {"id": 42}},
        ]
        self.assertEqual(resolve_primary_interface(netbox, raw_device()), "port1")
        netbox = Mock()
        netbox.get.return_value = {
            "address": "10.0.0.10/24",
            "assigned_object_type": None,
            "assigned_object_id": None,
        }
        with self.assertRaisesRegex(DeviceResolutionError, "management_interface_unresolved"):
            resolve_primary_interface(netbox, raw_device())


class MonitoringSecretTests(unittest.TestCase):
    def test_secret_is_device_specific_generated_once_and_written_with_cas(self):
        service = Mock()
        service.read_secret_with_metadata.side_effect = [
            DependencyError("secret_not_found"),
            ({"version": "2c", "community": "A" * 32, "managed_by": "atlas",
              "device_id": "42", "community_id": 7,
              "vdom": "root"}, {"version": 1}),
        ]
        service._authenticated.side_effect = lambda callback: callback("runtime-token")
        secrets = MonitoringSecrets(service)
        device = Device(42, "fw-1", "10.0.0.10", "fortinet", "fortios", "edge", "ba")
        value, version = secrets.ensure(device, 7, {"vdom": "root"})
        self.assertEqual(version, 1)
        self.assertEqual(value["device_id"], "42")
        callback = service._authenticated.call_args.args[0]
        service.client.kv_write.reset_mock()
        callback("runtime-token")
        service.client.kv_write.assert_called_once()
        args, kwargs = service.client.kv_write.call_args
        self.assertEqual(args[:2], ("runtime-token", "devices/snmp/fortinet/42/monitoring"))
        self.assertEqual(kwargs["cas"], 0)

    def test_cisco_cbs_secret_uses_documented_twenty_character_limit(self):
        service = Mock()
        stored = {
            "version": "2c",
            "community": "B" * 20,
            "managed_by": "atlas",
            "device_id": "43",
        }
        service.read_secret_with_metadata.side_effect = [
            DependencyError("secret_not_found"),
            (stored, {"version": 1}),
        ]
        service._authenticated.side_effect = lambda callback: callback("runtime-token")
        device = Device(
            43,
            "cbs-1",
            "10.0.0.11",
            "cisco",
            "cisco-cbs250-350",
            "edge",
            "ba",
        )

        value, _version = MonitoringSecrets(service).ensure(device)

        self.assertEqual(len(value["community"]), 20)
        payload = service.client.kv_write.call_args.args[2]
        self.assertEqual(len(payload["community"]), 20)
        self.assertNotIn("community_id", payload)


class FortiGateDriverTests(unittest.TestCase):
    def setUp(self):
        self.device = Device(42, "fw-1", "10.0.0.10", "fortinet", "fortios", "edge", "ba")
        self.options = {"https_port": 443, "snmp_port": 161, "default_vdom": "root", "vdom": "root", "interface": "port1",
                        "source_ips": ["10.0.0.5"], "tls_verify": True}
        self.driver = FortiGateMonitoringDriver(self.device, self.options, "secret-token", requester=Mock())

    def test_plan_preserves_customer_communities_and_detects_owned_conflict(self):
        state = {"sysinfo": {"status": "enable"}, "interface": {"allowaccess": "https snmp"},
                 "communities": [{"id": 2, "name": "customer"}]}
        secret = {"community_id": 7, "community": "AtlasCommunity", "vdom": "root"}
        self.assertEqual(self.driver.plan(state, secret), ["Create Atlas community restricted to monitoring sources"])
        state["communities"].append({"id": 7, "name": "someone-else"})
        with self.assertRaisesRegex(MonitoringError, "snmp_community_conflict"):
            self.driver.plan(state, secret)

    def test_desired_fortigate_state_has_no_second_run_changes(self):
        secret = {"community_id": 7, "community": "AtlasCommunity", "vdom": "root"}
        state = {"sysinfo": {"status": "enable"}, "interface": {"allowaccess": "https snmp"},
                 "communities": [self.driver.desired_community(secret)]}
        self.assertEqual(self.driver.plan(state, secret), [])

    def test_requests_use_bearer_no_redirects_and_tls_verification(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "success", "version": "v7.0.9", "results": {"status": "enable"}}
        self.driver.requester.request.return_value = response
        self.driver.request("GET", "system.snmp/sysinfo")
        kwargs = self.driver.requester.request.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secret-token"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["verify"])

    def test_vdom_is_resolved_from_mode_and_management_interface(self):
        self.driver.request = Mock(side_effect=[
            {"vdom-mode": "no-vdom"},
            {"name": "port1"},
        ])
        self.assertEqual(self.driver.resolve_vdom(), "root")
        self.assertEqual(self.driver.request.call_args_list[1].args[1], "system/interface")
        self.assertIsNone(self.driver.request.call_args_list[1].kwargs["vdom"])

        self.driver.request = Mock(side_effect=[
            {"vdom-mode": "multi-vdom"},
            {"name": "port1", "vdom": "root"},
        ])
        self.assertEqual(self.driver.resolve_vdom(), "root")

        self.driver.request = Mock(side_effect=[
            {"vdom-mode": "multi-vdom"},
            MonitoringError("fortigate_object_not_found", 404),
            [{"name": "port1", "vdom": "customer"}],
        ])
        self.assertEqual(self.driver.resolve_vdom(), "customer")

        self.driver.request = Mock(side_effect=[
            {"vdom-mode": "multi-vdom"},
            MonitoringError("fortigate_object_not_found", 404),
            [{"name": "port1", "vdom": "a"}, {"name": "port1", "vdom": "b"}],
        ])
        with self.assertRaisesRegex(MonitoringError, "vdom_ambiguous"):
            self.driver.resolve_vdom()

        self.driver.request = Mock(side_effect=[
            {"vdom-mode": "no-vdom"},
            [{"name": "port2"}],
        ])
        with self.assertRaisesRegex(MonitoringError, "management_interface_unresolved"):
            self.driver.resolve_vdom()


class AnsibleNetworkMonitoringDriverTests(unittest.TestCase):
    def test_write_intent_is_persisted_before_runner_can_fail(self):
        backend = Mock()
        backend.execute.side_effect = [
            {"compliant": False, "actions": []},
            MonitoringError("connection_timeout", 503),
        ]
        device = Device(
            50,
            "ios-1",
            "10.0.0.50",
            "cisco",
            "cisco-ios-xe",
            "edge",
            "ba",
        )
        driver = AnsibleNetworkMonitoringDriver(
            device,
            {"ssh_port": 22, "snmp_port": 161, "source_ips": ["10.0.0.5"]},
            {"username": "admin", "password": "secret"},
            SimpleNamespace(slug="cisco-ios-xe"),
            backend,
        )
        changes = []

        with self.assertRaisesRegex(MonitoringError, "connection_timeout"):
            driver.configure({"community": "A" * 32}, changes.append)

        self.assertEqual(changes, ["Configure Atlas SNMPv2c policy"])


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
    @staticmethod
    def config():
        return {"allowed_device_networks": ["10.0.0.0/24"],
            "fortios": {"default_https_port": 443, "default_snmp_port": 161,
                        "default_vdom": "root", "tls_verify": True},
            "monitoring": {"source_ip": "10.0.0.5"}}

    def test_resolution_uses_service_port_and_blocks_network_before_lookups(self):
        service = object.__new__(MonitoringService)
        service.netbox = Mock()
        service.platform_registry = AnsiblePlatformRegistry()
        service.netbox.get_device_services.return_value = [{"port_mappings": ["tcp/444"]}]
        service.netbox.get.side_effect = [
            {"address": "10.0.0.10/24", "assigned_object_type": "dcim.interface",
             "assigned_object_id": 7},
            {"id": 7, "name": "port1", "device": {"id": 42}},
        ]
        device = device_reference(raw_device())
        options = service._resolve_options(device, raw_device(), self.config())
        self.assertEqual((options["https_port"], options["interface"]), (444, "port1"))

        blocked = Device(42, "fw-1", "192.0.2.10", "fortinet", "fortios", "edge", "ba")
        service.netbox.reset_mock()
        with self.assertRaisesRegex(MonitoringError, "device_network_not_allowed"):
            service._resolve_options(blocked, raw_device(), self.config())
        service.netbox.get_device_services.assert_not_called()
        service.netbox.get.assert_not_called()

    def test_changed_inspection_marks_job_plan_stale_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            service = object.__new__(MonitoringService)
            service.store = JobStore(directory)
            plan_id = service.store.plan(
                {"device_id": 42, "mode": "monitor_only", "fingerprint": "old"}
            )
            job = service.store.create(42, "monitor_only")
            service.inspect = Mock(return_value=({}, "new", None))
            service.run(job["job_id"], plan_id)
            result = service.store.get(job["job_id"])
            self.assertEqual(result["error_code"], "plan_stale")
            self.assertFalse(result["mutation_attempted"])

    def test_desired_zabbix_state_has_no_second_run_changes(self):
        device = device_reference(raw_device())
        options = {"snmp_port": 161}
        reconciler = MonitoringZabbix(Mock(), device, options, "FortiGate by SNMP")
        version = 3
        wanted_interface = {**reconciler.interface(), "interfaceid": "20", "available": "1"}
        state = {
            "host": {"hostid": "10", "host": device.name, "name": device.name, "status": "0",
                     "monitored_by": "0", "proxyid": "0", "tags": [
                         {"tag": key, "value": value}
                         for key, value in reconciler.wanted_tags(version).items()
                     ], "hostgroups": [{"groupid": "30"}],
                     "parentTemplates": [{"templateid": "40"}],
                     "interfaces": [wanted_interface]},
            "group": {"groupid": "30"},
            "template": {"templateid": "40"},
            "proxy_id": None,
            "macro": {"type": "1", "description": reconciler.macro_description(version)},
            "item": {"type": "20", "snmp_oid": OID, "status": "0",
                     "interfaceid": "20", "key_": KEY},
        }
        self.assertEqual(reconciler.actions(state, version), [])

    def test_monitor_only_resolves_vdom_without_exposing_secrets(self):
        class Driver:
            token = None

            def __init__(self, _device, options, token):
                self.options = options
                Driver.token = token

            def resolve_vdom(self):
                self.options["vdom"] = "root"

            def check_schema(self):
                pass

            def inspect(self):
                return {"sysinfo": {"status": "enable"}, "communities": [],
                        "interface": {"allowaccess": "https snmp"}}

        class Client:
            def api_version(self):
                return "7.0.31"

            def host_get(self, _params):
                return []

            def template_get(self, _params):
                return [{"templateid": "40", "host": "FortiGate by SNMP"}]

            def hostgroup_get(self, _params):
                return [{"groupid": "30", "name": "Aricoma Atlas"}]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "monitoring.yaml"
            config.write_text(
                "version: 2\nallowed_device_networks: [10.0.0.0/24]\n"
                "fortios: {default_vdom: root, default_https_port: 443, default_snmp_port: 161, tls_verify: true}\n"
                "monitoring: {source_ip: 10.0.0.5}\n"
            )
            netbox = Mock()
            netbox.get.side_effect = [
                raw_device(),
                {"address": "10.0.0.10/24", "assigned_object_type": "dcim.interface",
                 "assigned_object_id": 7},
                {"id": 7, "name": "port1", "device": {"id": 42}},
            ]
            netbox.get_device_services.return_value = [{"port_mappings": ["tcp/444"]}]
            openbao = Mock()
            community = "A" * 32
            openbao.read_secret_with_metadata.return_value = (
                {"version": "2c", "community": community, "managed_by": "atlas",
                 "device_id": "42", "community_id": 7, "vdom": "root"},
                {"version": 2},
            )
            credential_source = Mock()
            credential_source.get_device_credentials.return_value = {"api_token": "api-secret"}
            zabbix = Mock()
            zabbix.client.return_value = Client()
            service = MonitoringService(
                netbox, openbao, zabbix, config, root / "jobs",
                driver_factory=Driver, credential_source=credential_source,
            )
            public, _, _ = service.inspect(42, "monitor_only")
            rendered = json.dumps(public)
            self.assertEqual(public["resolved"]["api_endpoint"], "https://10.0.0.10:444")
            self.assertEqual(public["resolved"]["vdom"], "root")
            self.assertEqual(
                public["resolved"]["credential_reference"],
                "devices/credentials/fortinet/edge/admin",
            )
            self.assertNotIn("api-secret", rendered)
            self.assertNotIn(community, rendered)
            self.assertEqual(Driver.token, "api-secret")
            credential_source.get_device_credentials.assert_called_once_with(
                "fortinet", "edge", authentication="api_token"
            )
            openbao.get_device_credentials.assert_not_called()

    def test_monitor_only_never_loads_management_credentials_or_generates_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "monitoring.yaml"
            config.write_text(
                "version: 2\n"
                "allowed_device_networks: [10.0.0.0/24]\n"
                "fortios: {default_vdom: root, default_https_port: 443, default_snmp_port: 161, tls_verify: true}\n"
                "monitoring: {source_ip: 10.0.0.5}\n",
                encoding="utf-8",
            )
            netbox = Mock()
            netbox.get.side_effect = [
                raw_device(),
                {"address": "10.0.0.10/24", "assigned_object_type": "dcim.interface",
                 "assigned_object_id": 7, "assigned_object": {"id": 7}},
                {"id": 7, "name": "port1", "device": {"id": 42}},
            ]
            netbox.get_device_services.return_value = []
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
