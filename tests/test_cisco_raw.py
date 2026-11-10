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

    @patch("ntc_templates.parse.parse_output", return_value=[{"version": "17.9"}])
    def test_textfsm_is_additive_and_preserves_raw_output(self, _parse):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-ios-xe")
        self.assertEqual("parsed", parsed.parser_status)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertEqual([{"version": "17.9"}], parsed.structured_data)

    def test_unverified_catalyst_textfsm_mapping_stays_raw_only(self):
        raw = classify_command_record(self.record(), max_output_bytes=4096)
        parsed = parse_command_output(raw, platform="cisco-catalyst-1200")
        self.assertEqual("template_missing", parsed.parser_status)
        self.assertEqual(raw.stdout, parsed.stdout)
        self.assertIsNone(parsed.structured_data)

    def test_site_filename_is_stable_and_utc(self):
        name = site_artifact_name(
            "Customer A", "DC Martin", datetime(2026, 11, 10, tzinfo=timezone.utc),
        )
        self.assertEqual("customer-a-dc-martin-20261110-000000Z.json", name)

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
            "platform": {"slug": "fortios"},
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


if __name__ == "__main__":
    unittest.main()
