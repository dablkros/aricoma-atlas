"""FortiOS SSH raw collection contract tests."""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from atlas.prophylaxis.cisco_raw import (
    CiscoRawDeviceReport,
    classify_command_record,
    site_artifact_name,
)
from atlas.prophylaxis.fortios_raw import (
    FORTIOS_FULL_COMMANDS,
    FORTIOS_IDENTITY_COMMANDS,
    FORTIOS_MAX_COMMANDS,
    FORTIOS_MAX_DISCOVERED_INTERFACES,
    FORTIOS_RAW_PLATFORMS,
    is_allowed_fortios_result_command,
)
from atlas.prophylaxis.run_store import FileRunStore
from atlas.prophylaxis.runs import DeviceFactsRunService


MODULE_PATH = Path(__file__).parents[1] / "automation" / "library" / "atlas_fortios_cli.py"


def load_fortios_module():
    spec = importlib.util.spec_from_file_location("atlas_fortios_cli_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FortiosRawContractTests(unittest.TestCase):
    def record(self, **overrides):
        payload = {
            "command": "get system status",
            "started_at": "2026-11-10T10:00:00Z",
            "finished_at": "2026-11-10T10:00:01Z",
            "stdout": "Version: FortiGate v7.4.0",
            "message": "",
            "failed": False,
        }
        payload.update(overrides)
        return payload

    def test_catalog_contains_all_mandatory_commands(self):
        self.assertEqual(3, len(FORTIOS_IDENTITY_COMMANDS))
        self.assertEqual(len(FORTIOS_FULL_COMMANDS), len(set(FORTIOS_FULL_COMMANDS)))
        for command in (
            "get system status", "get system performance status",
            "get system interface", "diagnose hardware sysinfo memory",
            "diagnose hardware deviceinfo disk", "diagnose hardware deviceinfo tpm",
            "diagnose hardware deviceinfo psu", "get system ha status",
            "get router info routing-table all", "get router info bgp network",
            "get system fortiguard-service status",
            "get vpn certificate local details", "get vpn certificate ca details",
            "diagnose vpn tunnel list", "execute log display",
        ):
            self.assertIn(command, FORTIOS_FULL_COMMANDS)

    def test_dynamic_nic_command_validation_rejects_injection(self):
        self.assertTrue(is_allowed_fortios_result_command(
            "diagnose hardware deviceinfo nic port1"
        ))
        for command in (
            "diagnose hardware deviceinfo nic port1; reboot",
            "diagnose hardware deviceinfo nic ../../etc/passwd",
            "diagnose hardware deviceinfo nic port 1",
        ):
            self.assertFalse(is_allowed_fortios_result_command(command))

    def test_interface_discovery_is_deduplicated_and_physical_only(self):
        module = load_fortios_module()
        output = """
== [ port1 ]
mode: static
status: up
== [ wan1 ]
mode: static
status: up
== [ port1 ]
mode: static
== [ vlan10 ]
type: vlan
== [ bad;name ]
mode: static
"""
        self.assertEqual(("port1", "wan1"), module._discover_interfaces(output))
        self.assertEqual(64, FORTIOS_MAX_DISCOVERED_INTERFACES)
        self.assertEqual(len(FORTIOS_FULL_COMMANDS) + 64, FORTIOS_MAX_COMMANDS)

    def test_interface_discovery_distinguishes_empty_from_unavailable(self):
        module = load_fortios_module()
        empty, empty_status = module._interface_discovery_result([{
            "command": "get system interface physical",
            "stdout": "",
            "failed": False,
        }])
        failed, failed_status = module._interface_discovery_result([{
            "command": "get system interface physical",
            "stdout": "command parse error before 'physical'",
            "failed": False,
        }])
        self.assertEqual((), empty)
        self.assertEqual("empty", empty_status)
        self.assertEqual((), failed)
        self.assertEqual("failed", failed_status)

    def test_interface_discovery_is_bounded_to_64(self):
        module = load_fortios_module()
        output = "\n".join(
            f"== [ port{index} ]\nmode: static" for index in range(70)
        )
        interfaces, status = module._interface_discovery_result([{
            "command": "get system interface physical",
            "stdout": output,
            "failed": False,
        }])
        self.assertEqual(64, len(interfaces))
        self.assertEqual("limited", status)

    def test_fortios_status_classification_is_conservative(self):
        unsupported = classify_command_record(
            self.record(stdout="command parse error before 'deviceinfo'"),
            max_output_bytes=4096, platform="fortios",
        )
        denied = classify_command_record(
            self.record(stdout="Permission denied"),
            max_output_bytes=4096, platform="fortios",
        )
        unknown_code = classify_command_record(
            self.record(stdout="Command fail. Return code -999", failed=True),
            max_output_bytes=4096, platform="fortios",
        )
        self.assertEqual("unsupported", unsupported.status)
        self.assertEqual("permission_denied", denied.status)
        self.assertEqual("error", unknown_code.status)

    def test_truncation_redacts_fortios_secret_and_private_key(self):
        result = classify_command_record(
            self.record(stdout=(
                'set psksecret "cleartext"\n'
                "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
                + ("x" * 5000)
            )),
            max_output_bytes=4096, platform="fortios",
        )
        self.assertEqual("truncated", result.status)
        self.assertNotIn("cleartext", result.stdout)
        self.assertNotIn("BEGIN PRIVATE KEY", result.stdout)
        self.assertLessEqual(len(result.stdout.encode()), 4096)

    def test_identity_and_full_collection_selection(self):
        self.assertEqual({"fortios"}, set(FORTIOS_RAW_PLATFORMS))
        identity = {
            "platform": {"slug": "fortios"},
            "custom_fields": {"profylaxia_checks": ["identity"]},
        }
        full = {
            "platform": {"slug": "fortios"},
            "custom_fields": {"profylaxia_checks": ["identity", "full_collection"]},
        }
        self.assertEqual("identity", DeviceFactsRunService._selected_raw_mode(identity))
        self.assertEqual("full_collection", DeviceFactsRunService._selected_raw_mode(full))

    def test_store_uses_vendor_neutral_site_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileRunStore(Path(temporary))
            run = store.create()
            report = CiscoRawDeviceReport.model_validate({
                "schema_version": "1.0", "run_id": str(run.run_id),
                "mode": "full_collection",
                "device": {
                    "netbox_id": 7, "hostname": "FGW01", "platform": "fortios",
                    "management_ip": "192.0.2.7",
                    "tenant": {"name": "Customer A", "slug": "customer-a"},
                    "site": {"name": "DC Martin", "slug": "dc-martin"},
                },
                "connection": {"transport": "ssh", "status": "failed"},
                "collection": {
                    "status": "failed", "total_commands": len(FORTIOS_FULL_COMMANDS),
                    "reason_code": "connection_failed",
                },
                "interface_discovery_status": "failed",
                "started_at": "2026-11-10T10:00:00Z",
                "finished_at": "2026-11-10T10:00:01Z",
            }).model_dump(mode="json")
            artifacts = store.write_site_reports(
                run.run_id, (report,),
                started_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
            )
            path = Path(temporary) / str(run.run_id) / "sites" / artifacts[0].artifact
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("atlas_network_site_collection", payload["document_type"])
            self.assertEqual("fortios", payload["devices"][0]["device"]["platform"])
            self.assertEqual(
                "customer-a-dc-martin-20261110-000000Z.json",
                site_artifact_name(
                    "Customer A", "DC Martin",
                    datetime(2026, 11, 10, tzinfo=timezone.utc),
                ),
            )

    def test_mixed_cisco_and_fortios_share_one_site_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileRunStore(Path(temporary))
            run = store.create()

            def report(device_id, hostname, platform):
                return CiscoRawDeviceReport.model_validate({
                    "schema_version": "1.0",
                    "run_id": str(run.run_id),
                    "mode": "identity",
                    "device": {
                        "netbox_id": device_id,
                        "hostname": hostname,
                        "platform": platform,
                        "management_ip": f"192.0.2.{device_id}",
                        "tenant": {"name": "Customer A", "slug": "customer-a"},
                        "site": {"name": "DC Martin", "slug": "dc-martin"},
                    },
                    "connection": {"transport": "ssh", "status": "failed"},
                    "collection": {
                        "status": "failed",
                        "total_commands": 3,
                        "reason_code": "connection_failed",
                    },
                    "started_at": "2026-11-10T10:00:00Z",
                    "finished_at": "2026-11-10T10:00:01Z",
                }).model_dump(mode="json")

            artifacts = store.write_site_reports(
                run.run_id,
                (
                    report(7, "FGW01", "fortios"),
                    report(8, "SW01", "cisco-ios-xe"),
                ),
                started_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(1, len(artifacts))
            self.assertEqual(2, artifacts[0].device_count)
            payload = json.loads((
                Path(temporary)
                / str(run.run_id)
                / "sites"
                / artifacts[0].artifact
            ).read_text(encoding="utf-8"))
            self.assertEqual(
                "atlas_network_site_collection", payload["document_type"]
            )


if __name__ == "__main__":
    unittest.main()
