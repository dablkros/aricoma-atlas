import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import requests
import yaml
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.integrations.zabbix import ZabbixAPIError, ZabbixClient
from atlas.services.zabbix import MetricPoint, ZabbixMonitoringService
from atlas.services.zabbix_sync import (
    ZabbixSyncResult,
    ZabbixSyncSummary,
)
from atlas.services.zabbix_sync import ZabbixSyncService
from scripts import deploy_zabbix, zabbix_admin, zabbix_cli


ROOT = Path(__file__).resolve().parents[1]
COMMUNITY = "test-community-never-log"


class FakeResponse:
    def __init__(self, status_code=200, body=None, json_error=None):
        self.status_code = status_code
        self.body = body
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return copy.deepcopy(self.body)


class QueueRequester:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ZabbixClientTests(unittest.TestCase):
    def test_json_rpc_success_uses_bearer_without_token_in_payload(self):
        requester = QueueRequester(
            FakeResponse(body={"jsonrpc": "2.0", "result": "7.0.31", "id": 1})
        )
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=requester,
        )

        self.assertEqual(client.api_version(), "7.0.31")
        _url, kwargs = requester.calls[0]
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertNotIn("api-token-value", json.dumps(kwargs["json"]))

        requester = QueueRequester(
            FakeResponse(body={"jsonrpc": "2.0", "result": [], "id": 1})
        )
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=requester,
        )
        self.assertEqual(client.host_get(), [])
        self.assertEqual(
            requester.calls[0][1]["headers"]["Authorization"],
            "Bearer api-token-value",
        )

    def test_admin_session_is_sent_in_json_rpc_auth_field(self):
        requester = QueueRequester(
            FakeResponse(body={"jsonrpc": "2.0", "result": [], "id": 1})
        )
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "admin-session-value",
            2,
            8,
            requester=requester,
            auth_mode="session",
        )

        self.assertEqual(client.host_get(), [])
        _url, kwargs = requester.calls[0]
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"]["auth"], "admin-session-value")

    def test_call_can_override_only_the_read_timeout(self):
        requester = QueueRequester(
            FakeResponse(body={"jsonrpc": "2.0", "result": {}, "id": 1}),
            FakeResponse(body={"jsonrpc": "2.0", "result": {}, "id": 2}),
        )
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=requester,
        )

        client.call("configuration.import", {}, read_timeout=120)
        client.call("host.get", {})

        self.assertEqual(requester.calls[0][1]["timeout"], (2, 120.0))
        self.assertEqual(requester.calls[1][1]["timeout"], (2, 8))

    def test_call_rejects_invalid_read_timeout_override(self):
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=QueueRequester(),
        )

        for value in (True, 0, -1, "120"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "read timeout"):
                    client.call("configuration.import", {}, read_timeout=value)

    def test_unknown_authentication_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "authentication mode"):
            ZabbixClient(
                "https://zabbix.example.test/api_jsonrpc.php",
                "token",
                2,
                8,
                auth_mode="cookie",
            )

    def test_http_rpc_timeout_auth_and_invalid_json_are_safe(self):
        cases = (
            (FakeResponse(status_code=500), "http_error"),
            (
                FakeResponse(
                    body={
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32602,
                            "message": "Invalid params.",
                            "data": "secret community is invalid",
                        },
                        "id": 1,
                    }
                ),
                "api_error",
            ),
            (requests.Timeout("api-token-value"), "connection_failed"),
            (FakeResponse(status_code=401), "authentication_failed"),
            (
                FakeResponse(
                    body={
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32602,
                            "message": "Invalid params.",
                            "data": "Login name or password is incorrect.",
                        },
                        "id": 1,
                    }
                ),
                "authentication_failed",
            ),
            (FakeResponse(json_error=ValueError("bad")), "invalid_response"),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                client = ZabbixClient(
                    "https://zabbix.example.test/api_jsonrpc.php",
                    "api-token-value",
                    2,
                    8,
                    requester=QueueRequester(response),
                )
                with self.assertRaises(ZabbixAPIError) as raised:
                    client.host_get()
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn("api-token-value", str(raised.exception))
                self.assertNotIn("community", str(raised.exception))

    def test_explicit_api_error_detail_is_bounded_and_not_in_exception_text(self):
        requester = QueueRequester(
            FakeResponse(
                body={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": "Invalid params.",
                        "data": "Invalid parameter   /1/uuid:\n UUIDv4 is expected."
                        + "x" * 600,
                    },
                    "id": 1,
                }
            )
        )
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=requester,
        )

        with self.assertRaises(ZabbixAPIError) as raised:
            client.call("configuration.import", {}, error_detail=True)

        self.assertEqual(raised.exception.code, "api_error")
        self.assertTrue(raised.exception.detail.startswith("Invalid parameter /1/uuid"))
        self.assertLessEqual(len(raised.exception.detail), 512)
        self.assertEqual(str(raised.exception), "api_error")

    def test_explicit_timeout_detail_is_safe_and_uses_effective_read_timeout(self):
        client = ZabbixClient(
            "https://zabbix.example.test/api_jsonrpc.php",
            "api-token-value",
            2,
            8,
            requester=QueueRequester(requests.Timeout("secret-value")),
        )

        with self.assertRaises(ZabbixAPIError) as raised:
            client.call(
                "configuration.import",
                {},
                error_detail=True,
                read_timeout=120,
            )

        self.assertEqual(raised.exception.code, "connection_failed")
        self.assertEqual(raised.exception.detail, "request timed out after 120s")
        self.assertNotIn("secret-value", str(raised.exception))


class FakeNetBox:
    def __init__(self, devices):
        self.devices = copy.deepcopy(devices)

    def get_monitoring_devices(self):
        return copy.deepcopy(self.devices)


class FakeOpenBao:
    def get_snmp_credentials(self, vendor, profile):
        if (vendor, profile) not in {
            ("cisco", "default"),
            ("sophos", "default"),
        }:
            raise AssertionError("unexpected credential lookup")
        return {"version": "2c", "community": COMMUNITY}, 7


class FakeGeneratedMonitoringSecrets:
    def read(self, device):
        if (device.vendor, device.id) == ("fortinet", 123):
            return ({"version": "2c", "community": "generated-community"}, 9)
        return None


class FakeZabbixClient:
    def __init__(self, hosts=()):
        self.hosts = copy.deepcopy(list(hosts))
        self.created = []
        self.updated = []
        self.host_queries = []

    def hostgroup_get(self, _params):
        return [{"groupid": "10", "name": "Aricoma Atlas"}]

    def template_get(self, params):
        return [
            {"templateid": str(index), "host": name, "name": name}
            for index, name in enumerate(params["filter"]["host"], start=20)
        ]

    def host_get(self, params):
        self.host_queries.append(copy.deepcopy(params))
        return copy.deepcopy(self.hosts)

    def host_create(self, payload):
        self.created.append(copy.deepcopy(payload))
        return "100"

    def host_update(self, payload):
        self.updated.append(copy.deepcopy(payload))
        return str(payload["hostid"])


class FakeZabbixService:
    def __init__(self, client):
        self._client = client

    def client(self):
        return self._client


def netbox_device(**overrides):
    value = {
        "id": 123,
        "name": "SW01",
        "status": {"value": "active"},
        "platform": {"slug": "cisco-ios"},
        "device_type": {"manufacturer": {"slug": "cisco"}},
        "site": {"slug": "bratislava"},
        "primary_ip4": {"address": "192.0.2.10/24"},
        "custom_fields": {
            "monitoring_enabled": True,
            "credential_profile": "default",
        },
    }
    value.update(overrides)
    return value


def managed_host(**overrides):
    value = {
        "hostid": "50",
        "host": "SW01",
        "name": "SW01",
        "status": "0",
        "interfaces": [
            {
                "interfaceid": "60",
                "type": "2",
                "ip": "192.0.2.10",
                "port": "161",
                "details": {
                    "version": "2",
                    "bulk": "1",
                    "community": "{$SNMP_COMMUNITY}",
                },
            }
        ],
        "hostgroups": [{"groupid": "10", "name": "Aricoma Atlas"}],
        "parentTemplates": [
            {"templateid": "22", "host": "Cisco IOS by SNMP"}
        ],
        "tags": [
            {"tag": "managed_by", "value": "aricoma-atlas"},
            {"tag": "netbox_id", "value": "123"},
            {"tag": "atlas_platform", "value": "cisco-ios"},
            {"tag": "atlas_site", "value": "bratislava"},
            {"tag": "atlas_snmp_secret_version", "value": "7"},
        ],
    }
    value.update(overrides)
    return value


class ZabbixSyncTests(unittest.TestCase):
    def sync(self, devices, hosts=()):
        client = FakeZabbixClient(hosts)
        service = ZabbixSyncService(
            FakeNetBox(devices),
            FakeOpenBao(),
            FakeZabbixService(client),
        )
        return service.sync(), client

    def test_create_is_idempotent_payload_with_secret_macro_and_ownership(self):
        result, client = self.sync([netbox_device()])

        self.assertEqual(result.summary.created, 1)
        self.assertEqual(result.created, ("SW01",))
        payload = client.created[0]
        self.assertEqual(payload["interfaces"][0]["ip"], "192.0.2.10")
        self.assertEqual(payload["macros"][0]["value"], COMMUNITY)
        self.assertEqual(payload["macros"][0]["type"], 1)
        self.assertIn(
            {"tag": "managed_by", "value": "aricoma-atlas"},
            payload["tags"],
        )
        self.assertIn({"tag": "netbox_id", "value": "123"}, payload["tags"])
        self.assertEqual(client.host_queries[0]["tags"][0]["operator"], 1)
        self.assertNotIn(COMMUNITY, repr(result))

    def test_generated_device_secret_precedes_profile_secret(self):
        client = FakeZabbixClient()
        service = ZabbixSyncService(
            FakeNetBox([
                netbox_device(
                    platform={"slug": "fortios"},
                    device_type={"manufacturer": {"slug": "fortinet"}},
                )
            ]),
            FakeOpenBao(),
            FakeZabbixService(client),
            monitoring_secrets=FakeGeneratedMonitoringSecrets(),
        )

        result = service.sync()

        self.assertEqual(result.summary.created, 1)
        self.assertEqual(client.created[0]["macros"][0]["value"], "generated-community")
        self.assertIn(
            {"tag": "atlas_snmp_secret_version", "value": "9"},
            client.created[0]["tags"],
        )

    def test_sophos_firewall_uses_managed_template_and_access_point_uses_generic(self):
        devices = [
            netbox_device(
                id=201,
                name="XGS-01",
                platform={"slug": "sophos-sfos"},
                device_type={"manufacturer": {"slug": "sophos"}},
            ),
            netbox_device(
                id=202,
                name="AP6-01",
                platform={"slug": "sophos-ap"},
                device_type={"manufacturer": {"slug": "sophos"}},
            ),
        ]

        result, client = self.sync(devices)

        self.assertEqual(result.summary.created, 2)
        self.assertEqual(
            {payload["host"] for payload in client.created},
            {"XGS-01", "AP6-01"},
        )
        by_host = {payload["host"]: payload for payload in client.created}
        self.assertEqual(by_host["XGS-01"]["templates"], [{"templateid": "21"}])
        self.assertEqual(by_host["AP6-01"]["templates"], [{"templateid": "25"}])

    def test_cbs250_350_uses_managed_template_and_legacy_cbs_uses_generic(self):
        devices = [
            netbox_device(
                id=301,
                name="CBS350-01",
                platform={"slug": "cisco-cbs250-350"},
            ),
            netbox_device(
                id=302,
                name="CBS220-01",
                platform={"slug": "cisco-cbs"},
            ),
        ]

        result, client = self.sync(devices)

        self.assertEqual(result.summary.created, 2)
        by_host = {payload["host"]: payload for payload in client.created}
        self.assertEqual(by_host["CBS350-01"]["templates"], [{"templateid": "20"}])
        self.assertEqual(by_host["CBS220-01"]["templates"], [{"templateid": "25"}])

    def test_sophos_red_is_not_silently_treated_as_snmp_firewall(self):
        device = netbox_device(
            name="RED-01",
            platform={"slug": "sophos-red"},
            device_type={"manufacturer": {"slug": "sophos"}},
        )

        result, client = self.sync([device])

        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.issues[0].code, "unsupported_platform")
        self.assertEqual(client.created, [])

    def test_unchanged_host_is_noop(self):
        result, client = self.sync([netbox_device()], [managed_host()])

        self.assertEqual(result.summary.unchanged, 1)
        self.assertEqual(client.created, [])
        self.assertEqual(client.updated, [])

    def test_ip_and_hostname_changes_update_existing_host(self):
        for device in (
            netbox_device(primary_ip4={"address": "192.0.2.99/24"}),
            netbox_device(name="SW01-RENAMED"),
        ):
            with self.subTest(name=device["name"], ip=device["primary_ip4"]):
                result, client = self.sync([device], [managed_host()])
                self.assertEqual(result.summary.updated, 1)
                self.assertEqual(client.updated[0]["hostid"], "50")
                self.assertEqual(
                    client.updated[0]["interfaces"][0]["interfaceid"],
                    "60",
                )

    def test_missing_or_disabled_netbox_device_disables_only_managed_host(self):
        result, client = self.sync([], [managed_host()])

        self.assertEqual(result.disabled, ("SW01",))
        self.assertEqual(client.updated, [{"hostid": "50", "status": 1}])

    def test_manual_hosts_are_outside_the_reconciliation_query(self):
        result, client = self.sync([])

        self.assertEqual(result.summary.total, 0)
        self.assertEqual(client.updated, [])
        self.assertEqual(
            client.host_queries[0]["tags"],
            [
                {
                    "tag": "managed_by",
                    "value": "aricoma-atlas",
                    "operator": 1,
                }
            ],
        )

    def test_invalid_device_is_reported_without_disabling_its_existing_host(self):
        invalid = netbox_device(
            id=124,
            name="BROKEN",
            primary_ip4=None,
            primary_ip6=None,
            primary_ip=None,
        )
        existing = managed_host(
            hostid="51",
            host="BROKEN",
            name="BROKEN",
            tags=[
                {"tag": "managed_by", "value": "aricoma-atlas"},
                {"tag": "netbox_id", "value": "124"},
            ],
        )

        result, client = self.sync([netbox_device(), invalid], [existing])

        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.summary.created, 1)
        self.assertEqual(result.summary.errors, 1)
        self.assertEqual(result.issues[0].code, "missing_primary_ip")
        self.assertEqual(client.updated, [])


class ZabbixDeploymentTests(unittest.TestCase):
    def test_managed_sophos_template_uses_current_mib_and_generic_link(self):
        path = ROOT / "catalog/zabbix/templates/sophos_sfos_by_snmp.yaml"
        source = path.read_text(encoding="utf-8")
        document = yaml.safe_load(source)
        template = document["zabbix_export"]["templates"][0]

        self.assertEqual(document["zabbix_export"]["version"], "7.0")
        self.assertEqual(template["template"], "Aricoma Atlas Sophos SFOS by SNMP")
        self.assertEqual(
            template["templates"],
            [{"name": "Network Generic Device by SNMP"}],
        )
        self.assertIn("1.3.6.1.4.1.2604.5", source)
        self.assertNotIn("snmp_oid: 'get[1.3.6.1.4.1.21067", source)

        def collect_uuids(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "uuid":
                        yield child
                    yield from collect_uuids(child)
            elif isinstance(value, list):
                for child in value:
                    yield from collect_uuids(child)

        template_uuids = list(collect_uuids(document["zabbix_export"]))
        self.assertTrue(template_uuids)
        self.assertTrue(all(uuid.UUID(value).version == 4 for value in template_uuids))

    def test_managed_cisco_cbs_template_uses_documented_oids_and_graphs(self):
        path = ROOT / "catalog/zabbix/templates/cisco_cbs250_350_by_snmp.yaml"
        source = path.read_text(encoding="utf-8")
        document = yaml.safe_load(source)
        template = document["zabbix_export"]["templates"][0]

        self.assertEqual(document["zabbix_export"]["version"], "7.0")
        self.assertEqual(
            template["template"],
            "Aricoma Atlas Cisco CBS250-350 by SNMP",
        )
        self.assertEqual(
            template["templates"],
            [{"name": "Network Generic Device by SNMP"}],
        )
        for oid in (
            "1.3.6.1.4.1.9.6.1.101.1.7.0",
            "1.3.6.1.4.1.9.6.1.101.1.8.0",
            "1.3.6.1.4.1.9.6.1.101.1.9.0",
            "1.3.6.1.4.1.9.6.1.101.53.15.1.10.1",
        ):
            self.assertIn(oid, source)
        self.assertEqual(
            {graph["name"] for graph in document["zabbix_export"]["graphs"]},
            {"Cisco CBS: CPU utilization", "Cisco CBS: Temperature"},
        )

        def collect_uuids(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "uuid":
                        yield child
                    yield from collect_uuids(child)
            elif isinstance(value, list):
                for child in value:
                    yield from collect_uuids(child)

        template_uuids = list(collect_uuids(document["zabbix_export"]))
        self.assertTrue(template_uuids)
        self.assertTrue(all(uuid.UUID(value).version == 4 for value in template_uuids))

    def test_managed_template_import_is_idempotent(self):
        admin = Mock()

        deploy_zabbix._import_managed_templates(admin, 120)

        self.assertEqual(admin.call.call_count, 2)
        imported_sources = []
        for import_call in admin.call.call_args_list:
            method, params = import_call.args
            self.assertEqual(method, "configuration.import")
            self.assertEqual(params["format"], "yaml")
            self.assertTrue(import_call.kwargs["error_detail"])
            self.assertEqual(import_call.kwargs["read_timeout"], 120)
            self.assertTrue(params["rules"]["templates"]["createMissing"])
            self.assertTrue(params["rules"]["templates"]["updateExisting"])
            self.assertTrue(params["rules"]["graphs"]["createMissing"])
            imported_sources.append(params["source"])
        self.assertIn("Aricoma Atlas Cisco CBS250-350 by SNMP", imported_sources[0])
        self.assertIn("Aricoma Atlas Sophos SFOS by SNMP", imported_sources[1])

    def test_managed_template_import_reports_zabbix_validation_detail(self):
        admin = Mock()
        admin.call.side_effect = ZabbixAPIError(
            "api_error",
            detail='Invalid parameter "/1/uuid": UUIDv4 is expected.',
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "UUIDv4 is expected",
        ):
            deploy_zabbix._import_managed_templates(admin, 120)

    def test_configuration_is_explicitly_pinned_and_matches_mapping(self):
        config = deploy_zabbix.load_config(ROOT / "deployment/zabbix.yaml")

        self.assertEqual(config["zabbix"]["version"], "7.0.31")
        self.assertEqual(config["runtime"]["template_import_timeout"], 120)
        for field in ("server_image", "web_image", "postgres_image"):
            self.assertNotIn("latest", config["zabbix"][field])
        self.assertTrue(config["zabbix"]["server_image"].endswith(":alpine-7.0.31"))
        self.assertTrue(config["zabbix"]["web_image"].endswith(":alpine-7.0.31"))

    def test_atlasctl_status_output_cannot_pollute_runtime_environment(self):
        text = (ROOT / "bin/atlasctl").read_text(encoding="utf-8")
        environment_function = text.split("atlas_env() {", 1)[1].split(
            "prepare_oxidized_inventory_runtime() {", 1
        )[0]
        status_function = text.split("show_status() {", 1)[1].split(
            "oxidized_logs() {", 1
        )[0]

        self.assertNotIn("Zabbix:", environment_function)
        self.assertIn("Zabbix:", status_function)

    def test_runtime_is_repeatable_loopback_only_and_compose_hides_password(self):
        config = deploy_zabbix.load_config(ROOT / "deployment/zabbix.yaml")
        secret = {
            "username": "zabbix",
            "database": "zabbix",
            "password": "database-password-never-in-compose-123456",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = deploy_zabbix.prepare_runtime(config, secret, root=root)
            before = first.read_text(encoding="utf-8")
            second = deploy_zabbix.prepare_runtime(config, secret, root=root)
            compose = yaml.safe_load(second.read_text(encoding="utf-8"))

            self.assertEqual(first, second)
            self.assertEqual(before, second.read_text(encoding="utf-8"))
            self.assertNotIn(secret["password"], before)
            self.assertEqual(compose["services"]["web"]["ports"], ["127.0.0.1:8082:8080"])
            self.assertNotIn("ports", compose["services"]["server"])
            self.assertNotIn("ports", compose["services"]["database"])
            self.assertTrue(compose["networks"]["database"]["internal"])
            self.assertEqual(
                compose["volumes"]["database_data"]["name"],
                "atlas-zabbix-database",
            )
            self.assertEqual(os.stat(second).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(second.parent).st_mode & 0o777, 0o700)
            self.assertEqual(
                os.stat(second.parent / "secrets").st_mode & 0o777,
                0o700,
            )
            for name in ("postgres-user", "postgres-password"):
                self.assertEqual(
                    os.stat(second.parent / "secrets" / name).st_mode & 0o777,
                    0o444,
                )

    def test_compose_failure_keeps_safe_docker_diagnostic(self):
        failure = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="dependency failed to start: container server is unhealthy\n",
        )
        with patch.object(deploy_zabbix.subprocess, "run", return_value=failure):
            with self.assertRaises(RuntimeError) as raised:
                deploy_zabbix.compose_step(
                    Path("/runtime/docker-compose.yml"),
                    "atlas-zabbix",
                    "container start",
                    "up",
                    "-d",
                )

        message = str(raised.exception)
        self.assertIn("Zabbix container start failed (exit 1)", message)
        self.assertIn("container server is unhealthy", message)

    def test_cli_returns_failure_for_partial_sync(self):
        response = {
            "status": "partial_success",
            "summary": {
                "created": 1,
                "updated": 0,
                "disabled": 0,
                "unchanged": 0,
                "errors": 1,
            },
            "issues": [{"device": "BROKEN", "code": "missing_primary_ip"}],
        }
        with patch.object(zabbix_cli, "request", return_value=response):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = zabbix_cli.print_sync()

        self.assertEqual(exit_code, 1)

    def test_admin_credentials_command_revokes_openbao_token(self):
        client = Mock()
        client.kv_read.return_value = {
            "username": "Admin",
            "password": "test-password-that-is-long-enough-123456",
        }
        with (
            patch.object(
                zabbix_admin,
                "connect_openbao",
                return_value=(client, "temporary-token"),
            ),
            patch.object(
                zabbix_admin,
                "load_config",
                return_value={
                    "proxy": {"https_port": 443},
                    "services": {
                        "zabbix": {"hostname": "zabbix.example.test"},
                    },
                },
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            zabbix_admin.main()

        self.assertIn("URL: https://zabbix.example.test/", output.getvalue())
        self.assertIn("Username: Admin", output.getvalue())
        self.assertIn("Password: test-password", output.getvalue())
        client.kv_read.assert_called_once_with("temporary-token", "zabbix/admin")
        client.revoke_self.assert_called_once_with("temporary-token")

    def test_bootstrap_always_closes_admin_session(self):
        admin = Mock()
        with patch.object(deploy_zabbix, "_admin_client", return_value=admin):
            with patch.object(
                deploy_zabbix,
                "_bootstrap_api_identity",
                side_effect=RuntimeError("bootstrap failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                    deploy_zabbix.bootstrap_api_identity({}, Mock(), "bao-token")

        admin.call.assert_called_once_with("user.logout", [])

    def test_admin_bootstrap_recovers_interrupted_password_rotation(self):
        desired_password = "stored-password-value-that-is-long-enough-123"
        unauthenticated = Mock()
        unauthenticated.call.side_effect = [
            ZabbixAPIError("authentication_failed"),
            "default-session",
            "rotated-session",
        ]
        default_admin = Mock()

        def admin_call(method, _params):
            if method == "user.get":
                return [{"userid": "1", "username": "Admin"}]
            return {"userids": ["1"]}

        default_admin.call.side_effect = admin_call
        rotated_admin = Mock()
        config = {"network": {"listen_address": "127.0.0.1", "host_port": 8082}}
        secret = {"username": "Admin", "password": desired_password}

        with patch.object(deploy_zabbix, "optional_secret", return_value=secret):
            with patch.object(
                deploy_zabbix,
                "ZabbixClient",
                side_effect=[unauthenticated, default_admin, rotated_admin],
            ):
                result = deploy_zabbix._admin_client(config, Mock(), "bao-token")

        self.assertIs(result, rotated_admin)
        self.assertIn(
            call(
                "user.update",
                {
                    "userid": "1",
                    "passwd": desired_password,
                    "current_passwd": "zabbix",
                },
            ),
            default_admin.call.mock_calls,
        )

    def test_admin_password_update_failure_has_safe_context(self):
        desired_password = "stored-password-value-that-is-long-enough-123"
        unauthenticated = Mock()
        unauthenticated.call.side_effect = [
            ZabbixAPIError("authentication_failed"),
            "default-session",
        ]
        default_admin = Mock()
        default_admin.call.side_effect = [
            [{"userid": "1", "username": "Admin"}],
            ZabbixAPIError("api_error"),
        ]
        config = {"network": {"listen_address": "127.0.0.1", "host_port": 8082}}
        secret = {"username": "Admin", "password": desired_password}

        with patch.object(deploy_zabbix, "optional_secret", return_value=secret):
            with patch.object(
                deploy_zabbix,
                "ZabbixClient",
                side_effect=[unauthenticated, default_admin],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Zabbix administrator password update failed \\(api_error\\)",
                ) as raised:
                    deploy_zabbix._admin_client(config, Mock(), "bao-token")

        self.assertNotIn(desired_password, str(raised.exception))


class MetricClient:
    def __init__(self):
        self.history_params = None
        self.trend_params = None

    def item_get(self, _params):
        return [
            {
                "itemid": "70",
                "key_": "system.cpu.util[avg1]",
                "name": "CPU utilization",
                "value_type": "0",
                "units": "%",
                "lastvalue": "23.5",
                "lastclock": "100",
                "status": "0",
                "state": "0",
            }
        ]

    def history_get(self, params):
        self.history_params = copy.deepcopy(params)
        return [{"clock": "90", "value": "20.0"}]

    def trend_get(self, params):
        self.trend_params = copy.deepcopy(params)
        return [{"clock": "80", "value_avg": "18.25"}]


class MetricServiceTests(unittest.TestCase):
    def test_history_and_trend_use_discovered_item_not_fixed_itemid(self):
        client = MetricClient()
        service = ZabbixMonitoringService(FakeZabbixService(client))

        history = service.metric("50", "cpu", "1h")
        trend = service.metric("50", "cpu", "7d")

        self.assertEqual(history["value"], 23.5)
        self.assertEqual(history["points"], [MetricPoint(90, 20.0)])
        self.assertEqual(client.history_params["itemids"], ["70"])
        self.assertEqual(client.history_params["history"], 0)
        self.assertEqual(trend["points"], [MetricPoint(80, 18.25)])
        self.assertEqual(client.trend_params["itemids"], ["70"])


class FakeMonitoring:
    def hosts(self):
        host = managed_host()
        host["interfaces"][0].update({"available": "1"})
        return [host]

    @staticmethod
    def _tags(host):
        return ZabbixSyncService._tags(host)

    def host_for_netbox_id(self, device_id):
        return self.hosts()[0] if device_id == 123 else None

    def problems(self, _hostids=None):
        return [
            {
                "eventid": "88",
                "name": "CPU high",
                "severity": "3",
                "clock": "1700000000",
                "acknowledged": "0",
            }
        ]

    def metric(self, _hostid, metric, window):
        if metric != "cpu":
            raise ValueError("unsupported_metric")
        self.last_window = window
        return {
            "metric": "cpu",
            "unit": "%",
            "value": 23.5,
            "points": [MetricPoint(1700000000, 20.0)],
        }


class FakeStatusClient:
    def api_version(self):
        return "7.0.31"

    def host_get(self, _params):
        return []


class FakeStatusService:
    def client(self):
        return FakeStatusClient()


class ZabbixApiTests(unittest.TestCase):
    def client(self):
        monitoring = FakeMonitoring()
        sync = Mock()
        sync.sync.return_value = ZabbixSyncResult(
            "success",
            ZabbixSyncSummary(1, 0, 0, 0, 1, 0),
            (),
            (),
            (),
            ("SW01",),
            (),
        )
        dependencies = SimpleNamespace(
            zabbix=FakeStatusService(),
            zabbix_monitoring=monitoring,
            zabbix_sync=sync,
        )
        settings = Settings(
            environment="test",
            log_level="CRITICAL",
            _env_file=None,
        )
        return (
            TestClient(
                create_app(settings, dependencies=dependencies),
                raise_server_exceptions=False,
            ),
            monitoring,
        )

    def test_status_sync_device_and_graph_json_are_secret_free(self):
        client, monitoring = self.client()

        status = client.get("/api/zabbix/status")
        sync = client.post("/api/zabbix/sync")
        device = client.get("/api/devices/123/monitoring")
        metric = client.get(
            "/api/devices/123/monitoring/metrics/cpu?window=7d"
        )

        self.assertEqual(status.json()["version"], "7.0.31")
        self.assertEqual(sync.json()["summary"]["unchanged"], 1)
        self.assertEqual(device.json()["status"], "up")
        self.assertEqual(device.json()["metrics"]["cpu"], 23.5)
        self.assertEqual(metric.json()["points"][0]["value"], 20.0)
        self.assertEqual(monitoring.last_window, "7d")
        serialized = "".join(
            response.text for response in (status, sync, device, metric)
        )
        self.assertNotIn(COMMUNITY, serialized)

    def test_unknown_host_and_metric_use_stable_errors(self):
        client, _monitoring = self.client()

        missing = client.get("/api/devices/999/monitoring")
        unsupported = client.get(
            "/api/devices/123/monitoring/metrics/not-real"
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["error"]["code"],
            "monitoring_host_not_found",
        )
        self.assertEqual(unsupported.status_code, 404)
        self.assertEqual(
            unsupported.json()["error"]["code"],
            "monitoring_metric_not_supported",
        )


if __name__ == "__main__":
    unittest.main()
