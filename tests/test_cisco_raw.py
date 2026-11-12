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
from atlas.prophylaxis.run_store import FileRunStore, RunNotFoundError
from atlas.prophylaxis.runs import DeviceFactsRunService

CISCO_REGISTRY_FIXTURES = Path(__file__).parent / "fixtures/cisco_registry"


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

    def test_legacy_command_views_are_derived_unique_registry_views(self):
        self.assertEqual(len(CISCO_FULL_COMMANDS), len(set(CISCO_FULL_COMMANDS)))
        self.assertEqual(len(CISCO_IDENTITY_COMMANDS), len(set(CISCO_IDENTITY_COMMANDS)))
        self.assertIn("show version", CISCO_IDENTITY_COMMANDS)
        self.assertIn("show inventory", CISCO_IDENTITY_COMMANDS)

    def test_supported_platforms_are_explicit(self):
        self.assertEqual({
            "cisco-ios", "cisco-ios-xe", "cisco-nxos", "cisco-asa",
            "cisco-cbs", "cisco-cbs250-350", "cisco-catalyst-1200",
            "cisco-catalyst-1300",
        }, set(CISCO_RAW_PLATFORMS))

    def test_platform_specific_commands_do_not_leak_between_operating_systems(self):
        self.assertIn("show vpc", cisco_raw_commands("cisco-nxos", "full_collection"))
        self.assertNotIn("show asp drop", cisco_raw_commands("cisco-nxos", "full_collection"))
        self.assertIn("show asp drop", cisco_raw_commands("cisco-asa", "full_collection"))
        self.assertNotIn("show vpc", cisco_raw_commands("cisco-asa", "full_collection"))
        self.assertIn(
            "show cpu utilization",
            cisco_raw_commands("cisco-catalyst-1200", "full_collection"),
        )
        ios_xe = cisco_raw_commands("cisco-ios-xe", "full_collection")
        for command in (
            "show clock detail", "show platform", "show file systems",
            "show ip interface brief", "show etherchannel summary",
            "show license all", "show ip bgp summary", "show power inline",
        ):
            self.assertIn(command, ios_xe)
        self.assertNotIn("show vpc", ios_xe)
        self.assertNotIn("show system resources", ios_xe)

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

    def test_historical_error_text_in_show_logging_is_not_execution_failure(self):
        result = classify_command_record(
            self.record(
                command="show logging",
                stdout="%AUTH-3-FAIL: Authorization failed for old session",
            ),
            max_output_bytes=4096,
        )
        self.assertEqual("success", result.status)

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

    def test_colon_form_proxy_password_is_redacted(self):
        result = classify_command_record(
            self.record(command="show license status", stdout="Proxy Password: TOP-SECRET"),
            max_output_bytes=4096,
        )
        self.assertEqual("Proxy Password: <redacted>", result.stdout)

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
            "atlas:parse_cisco_smb_running_config/1.0",
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

    @patch(
        "atlas.prophylaxis.cisco_raw._parse_textfsm_template",
        return_value=[{"version": "17.9"}],
    )
    def test_textfsm_is_additive_and_preserves_raw_output(self, _parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-ios-xe")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual("success", parsed.parsing_status)
        self.assertEqual("ntc_textfsm", parsed.parser_engine)
        self.assertEqual("cisco_ios", parsed.parser_platform)
        self.assertEqual("cisco_ios_show_version.textfsm", parsed.parser_template)
        self.assertEqual("9.3.0", parsed.parser_version)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertEqual([{"version": "17.9"}], parsed.structured_data)

    @patch(
        "atlas.prophylaxis.cisco_raw._parse_textfsm_template",
        return_value=[{"sw_version": "4.1.3.36"}],
    )
    def test_catalyst_1200_uses_reviewed_cisco_smb_textfsm_family(self, parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-catalyst-1200")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual("cisco_s300", parsed.parser_platform)
        self.assertEqual("cisco_s300_show_version.textfsm", parsed.parser_template)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertEqual([{"sw_version": "4.1.3.36"}], parsed.structured_data)
        definition = parse.call_args.args[0]
        self.assertEqual("show version", definition.command)
        self.assertEqual(raw.stdout, parse.call_args.args[1])

    def test_official_ios_transceiver_fixture_uses_explicit_template(self):
        raw = classify_command_record(self.record(
            command="show interfaces transceiver",
            stdout=(CISCO_REGISTRY_FIXTURES / "ios_transceiver.raw").read_text(),
        ), max_output_bytes=65536)
        parsed = parse_command_output(raw, platform="cisco-ios")
        self.assertEqual("success", parsed.parsing_status)
        self.assertEqual("cisco_ios_show_interface_transceiver.textfsm", parsed.parser_template)
        self.assertEqual("Te1/49", parsed.structured_data[0]["interface"])

    def test_official_nxos_interface_status_fixture_uses_explicit_template(self):
        raw = classify_command_record(self.record(
            command="show interface status",
            stdout=(CISCO_REGISTRY_FIXTURES / "nxos_interface_status.raw").read_text(),
        ), max_output_bytes=65536)
        parsed = parse_command_output(raw, platform="cisco-nxos")
        self.assertEqual("success", parsed.parsing_status)
        self.assertEqual("cisco_nxos_show_interface_status.textfsm", parsed.parser_template)
        self.assertEqual("Eth1/1", parsed.structured_data[0]["port"])
        self.assertIsNone(parsed.normalized_data["items"][0]["description"])

    def test_official_smb_system_fixture_is_parsed(self):
        raw = classify_command_record(self.record(
            command="show system",
            stdout=(CISCO_REGISTRY_FIXTURES / "smb_system.raw").read_text(),
        ), max_output_bytes=65536)
        parsed = parse_command_output(raw, platform="cisco-cbs250-350")
        self.assertEqual("success", parsed.parsing_status)
        self.assertEqual("ZOO-SF300-POE-2", parsed.structured_data[0]["hostname"])

    def test_official_asa_failover_fixture_is_parsed(self):
        raw = classify_command_record(self.record(
            command="show failover",
            stdout=(CISCO_REGISTRY_FIXTURES / "asa_failover.raw").read_text(),
        ), max_output_bytes=65536)
        parsed = parse_command_output(raw, platform="cisco-asa")
        self.assertEqual("success", parsed.parsing_status)
        self.assertEqual("cisco_asa_show_failover.textfsm", parsed.parser_template)
        self.assertEqual("Primary", parsed.structured_data[0]["role"])

    @patch(
        "atlas.prophylaxis.cisco_raw._parse_textfsm_template",
        side_effect=ValueError("fixture does not match"),
    )
    def test_parse_error_preserves_successful_collection_and_stdout(self, _parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-ios")
        self.assertEqual("success", parsed.status)
        self.assertEqual("parse_error", parsed.parsing_status)
        self.assertEqual("failed", parsed.parser_status)
        self.assertEqual(raw.stdout, parsed.stdout)

    @patch("atlas.prophylaxis.cisco_raw._parse_textfsm_template", return_value=[])
    def test_empty_parser_result_is_distinct_from_empty_command_output(self, _parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-ios")
        self.assertEqual("success", parsed.status)
        self.assertEqual("empty_result", parsed.parsing_status)

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
            store.save(run.model_copy(update={"raw_reports": artifacts}))
            self.assertEqual(payload, store.read_site_report(
                run.run_id, artifacts[0].artifact,
            ))
            self.assertEqual(run.run_id, store.list_runs(limit=1)[0].run_id)
            with self.assertRaises(RunNotFoundError):
                store.site_report_path(run.run_id, "unowned-report.json")


if __name__ == "__main__":
    unittest.main()
