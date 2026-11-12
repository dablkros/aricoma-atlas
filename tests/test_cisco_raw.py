import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from atlas.prophylaxis.cisco_raw import (
    CISCO_FULL_COMMANDS,
    CISCO_IDENTITY_COMMANDS,
    CISCO_RAW_PLATFORMS,
    CiscoRawDeviceReport,
    cisco_raw_commands,
    classify_command_record,
    parse_command_output,
    site_artifact_name,
)
from atlas.prophylaxis.run_store import FileRunStore
from atlas.prophylaxis.runs import DeviceFactsRunService


class CiscoRawContractTests(unittest.TestCase):
    def record(self, **overrides):
        payload = {
            "command": "show version",
            "started_at": "2026-11-10T10:00:00Z",
            "finished_at": "2026-11-10T10:00:01Z",
            "stdout": "Cisco IOS Software",
            "message": "",
            "failed": False,
        }
        payload.update(overrides)
        return payload

    def test_catalog_contains_exactly_49_unique_commands(self):
        self.assertEqual(49, len(CISCO_FULL_COMMANDS))
        self.assertEqual(49, len(set(CISCO_FULL_COMMANDS)))
        self.assertEqual(
            ("show version", "show inventory", "show hostname"),
            CISCO_IDENTITY_COMMANDS,
        )

    def test_supported_platforms_are_explicit(self):
        self.assertEqual({
            "cisco-ios", "cisco-ios-xe", "cisco-nxos", "cisco-asa",
            "cisco-cbs", "cisco-cbs250-350", "cisco-catalyst-1200",
            "cisco-catalyst-1300",
        }, set(CISCO_RAW_PLATFORMS))

    def test_known_unsupported_hostname_command_is_not_scheduled(self):
        for platform in (
            "cisco-ios", "cisco-ios-xe", "cisco-cbs250-350",
            "cisco-catalyst-1200", "cisco-catalyst-1300",
        ):
            with self.subTest(platform=platform):
                self.assertNotIn(
                    "show hostname", cisco_raw_commands(platform, "identity")
                )
        self.assertIn(
            "show hostname", cisco_raw_commands("cisco-nxos", "identity")
        )

    def test_command_statuses_are_distinct(self):
        unsupported = classify_command_record(
            self.record(stdout="% Invalid input detected"), max_output_bytes=4096,
        )
        denied = classify_command_record(
            self.record(stdout="Authorization failed"), max_output_bytes=4096,
        )
        empty = classify_command_record(
            self.record(stdout="SW01#"), max_output_bytes=4096,
        )
        timeout = classify_command_record(
            self.record(message="command timeout", stdout=""), max_output_bytes=4096,
        )
        self.assertEqual("unsupported", unsupported.status)
        self.assertEqual("permission_denied", denied.status)
        self.assertEqual("empty", empty.status)
        self.assertEqual("timeout", timeout.status)

    def test_large_output_is_truncated_and_common_secrets_are_redacted(self):
        result = classify_command_record(
            self.record(stdout="enable secret cleartext\n" + ("x" * 5000)),
            max_output_bytes=4096,
        )
        self.assertEqual("truncated", result.status)
        self.assertNotIn("cleartext", result.stdout)
        self.assertLessEqual(len(result.stdout.encode()), 4096)

    def test_cisco_smb_running_config_secrets_are_redacted(self):
        result = classify_command_record(
            self.record(
                command="show running-config",
                stdout=(
                    "hostname SW01\n"
                    "username operator password encrypted USER-HASH privilege 15\n"
                    "snmp-server community PRIVATE-COMMUNITY ro\n"
                    "encrypted cbd key id 123 secret CBD-SECRET\n"
                ),
            ),
            max_output_bytes=4096,
        )
        self.assertEqual("success", result.status)
        self.assertNotIn("USER-HASH", result.stdout)
        self.assertNotIn("PRIVATE-COMMUNITY", result.stdout)
        self.assertNotIn("CBD-SECRET", result.stdout)
        self.assertEqual(3, result.stdout.count("<redacted>"))

    def test_cisco_smb_running_config_has_safe_structured_summary(self):
        raw = classify_command_record(
            self.record(
                command="show running-config",
                stdout=(
                    "config-file-header\nSW01\nv3.3.0.16 / RCBS3.3_950\n"
                    "hostname SW01\nno ip routing\nip ssh server\n"
                    "snmp-server server\nno sntp unicast client enable\n"
                    "username operator password encrypted USER-HASH privilege 15\n"
                    "vlan database\nvlan 5,30,100-116\nvlan 801-803,888\nexit\n"
                    "interface vlan 5\n name management\ninterface vlan 30\n"
                ),
            ),
            max_output_bytes=4096,
        )
        parsed = parse_command_output(raw, platform="cisco-catalyst-1200")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual(
            "atlas:cisco-smb-running-config-summary/1.0",
            parsed.parser_template,
        )
        self.assertEqual([{
            "hostname": "SW01",
            "config_version": "3.3.0.16",
            "ip_routing_enabled": False,
            "ssh_server_enabled": True,
            "snmp_agent_enabled": True,
            "sntp_client_enabled": False,
            "configured_user_count": 1,
            "vlan_definitions": ["5,30,100-116", "801-803,888"],
            "vlan_interface_ids": ["5", "30"],
        }], parsed.structured_data)
        self.assertNotIn("USER-HASH", parsed.stdout)

    @patch("ntc_templates.parse.parse_output", return_value=[{"version": "17.9"}])
    def test_textfsm_is_additive_and_preserves_raw_output(self, _parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-ios-xe")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertEqual([{"version": "17.9"}], parsed.structured_data)

    @patch("ntc_templates.parse.parse_output", return_value=[{"sw_version": "4.1.3.36"}])
    def test_catalyst_1200_uses_reviewed_cisco_smb_textfsm_family(self, parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-catalyst-1200")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertEqual([{"sw_version": "4.1.3.36"}], parsed.structured_data)
        parse.assert_called_once_with(
            platform="cisco_s300", command="show version", data=raw.stdout,
        )

    def test_site_filename_is_stable_and_utc(self):
        name = site_artifact_name(
            "Customer A", "DC Martin", datetime(2026, 11, 10, tzinfo=timezone.utc),
        )
        self.assertEqual("customer-a-dc-martin-20261110-000000Z.json", name)

    def test_site_filename_does_not_repeat_tenant_prefixed_site_slug(self):
        name = site_artifact_name(
            "aricoma-systems", "aricoma-systems-dcmartin",
            datetime(2026, 11, 11, 14, 30, 23, tzinfo=timezone.utc),
        )
        self.assertEqual("aricoma-systems-dcmartin-20261111-143023Z.json", name)

    def test_full_collection_takes_precedence_over_identity(self):
        device = {
            "platform": {"slug": "cisco-nxos"},
            "custom_fields": {
                "profylaxia_checks": ["identity", "full_collection"],
            },
        }
        self.assertEqual(
            "full_collection",
            DeviceFactsRunService._selected_raw_mode(device),
        )

    def test_non_cisco_platform_does_not_enter_raw_mode(self):
        device = {
            "platform": {"slug": "aruba-aos-cx"},
            "custom_fields": {"profylaxia_checks": ["full_collection"]},
        }
        self.assertIsNone(DeviceFactsRunService._selected_raw_mode(device))

    def test_store_groups_reports_by_tenant_and_site(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = FileRunStore(Path(temporary))
            run = store.create()
            report = CiscoRawDeviceReport.model_validate({
                "schema_version": "1.0",
                "run_id": str(run.run_id),
                "mode": "identity",
                "device": {
                    "netbox_id": 7,
                    "hostname": "SW01",
                    "platform": "cisco-ios-xe",
                    "management_ip": "192.0.2.7",
                    "tenant": {"name": "Customer A", "slug": "customer-a"},
                    "site": {"name": "DC Martin", "slug": "dc-martin"},
                },
                "connection": {"transport": "ssh", "status": "failed"},
                "collection": {
                    "status": "failed",
                    "total_commands": 3,
                    "reason_code": "connection_failed",
                },
                "outputs": {},
                "ignored_commands": [],
                "started_at": "2026-11-10T10:00:00Z",
                "finished_at": "2026-11-10T10:00:01Z",
            }).model_dump(mode="json")
            artifacts = store.write_site_reports(
                run.run_id,
                (report,),
                started_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(1, len(artifacts))
            self.assertEqual(1, artifacts[0].device_count)
            self.assertTrue(
                (Path(temporary) / str(run.run_id) / "sites" / artifacts[0].artifact).is_file()
            )
            payload = json.loads(
                (
                    Path(temporary)
                    / str(run.run_id)
                    / "sites"
                    / artifacts[0].artifact
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                "atlas_cisco_site_collection", payload["document_type"]
            )


if __name__ == "__main__":
    unittest.main()
