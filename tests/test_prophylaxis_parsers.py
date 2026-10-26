import unittest
from pathlib import Path

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import (
    CheckId,
    CollectorId,
    EvaluationOutcome,
    RawCheckResult,
    RawCollectorResult,
)
from atlas.prophylaxis.parsers import (
    normalize_cpu,
    normalize_device_facts,
    normalize_environment_health,
    normalize_firmware_recommendation,
    normalize_ha_status,
    normalize_interface_errors,
    normalize_interface_state,
    normalize_license_state,
    normalize_memory,
    normalize_ntp_status,
    normalize_pki_certificates,
    normalize_recent_logs,
    normalize_storage_health,
    normalize_uptime,
)


FIXTURES = Path(__file__).parent / "fixtures/prophylaxis"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ProphylaxisParserTests(unittest.TestCase):
    @staticmethod
    def command_result(platform, collector, **outputs):
        return RawCollectorResult(
            collector=collector,
            platform=platform,
            status="ok",
            data=outputs,
        )

    def test_cisco_ios_and_cbs_parsers_remain_platform_specific(self):
        ios = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="cisco-ios",
            status="ok",
            raw_output=fixture("cisco_ios_cpu.txt"),
        )
        cbs = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="cisco-cbs",
            status="ok",
            raw_output=fixture("cisco_cbs_cpu.txt"),
        )
        cbs250_350 = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="cisco-cbs250-350",
            status="ok",
            raw_output=fixture("cisco_cbs_cpu.txt"),
        )

        self.assertEqual(normalize_cpu(ios).current_percent, 8.0)
        self.assertEqual(normalize_cpu(cbs).five_minute_percent, 3.0)
        self.assertEqual(normalize_cpu(cbs250_350).five_minute_percent, 3.0)

    def test_fortios_structured_result_is_not_converted_to_text(self):
        raw = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="fortios",
            status="ok",
            data={
                "cpu": [
                    {
                        "current": 9,
                        "historical": {"1-min": {"average": 7}},
                    }
                ]
            },
        )

        values = normalize_cpu(raw)

        self.assertEqual(values.current_percent, 9.0)
        self.assertEqual(values.one_minute_percent, 7.0)
        self.assertIsNone(values.five_minute_percent)

    def test_fortios_complete_catalog_without_upgrade_is_not_a_recommendation(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_COMPLIANCE,
            get_system_status=(
                "Version: FortiGate-VM64-KVM v8.0.2,build0251,260928 (GA.F)"
            ),
            diagnose_fdsm_image_list=(
                "Last update: 895 secs ago. Total: 3\n"
                "FGT_VM64_KVM-v8.0.1-FORTINET.out v8.0 P1 b0245 (downgrade)\n"
                "FGT_VM64_KVM-v8.0-FORTINET.out v8.0 b0167 (downgrade)\n"
                "FGT_VM64_KVM-v7.6.8-FORTINET.out v7.6 P8 b3744 (downgrade)"
            ),
        )

        values = normalize_firmware_recommendation(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertFalse(values.metrics["firmware_update_available"])
        self.assertIsNone(values.metrics["firmware_recommended_version"])
        self.assertTrue(values.metrics["catalog_complete"])
        self.assertEqual(values.metrics["downgrade_image_count"], 3)

    def test_fortios_catalog_with_upgrade_reports_available_image(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_COMPLIANCE,
            get_system_status="Version: FortiGate-VM64-KVM v8.0.1,build0245",
            diagnose_fdsm_image_list=(
                "Last update: 10 secs ago. Total: 2\n"
                "FGT_VM64_KVM-v8.0.2-FORTINET.out v8.0 P2 b0251 (upgrade)\n"
                "FGT_VM64_KVM-v8.0-FORTINET.out v8.0 b0167 (downgrade)"
            ),
        )

        values = normalize_firmware_recommendation(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.WARNING)
        self.assertTrue(values.metrics["firmware_update_available"])
        self.assertEqual(values.metrics["upgrade_image_count"], 1)
        self.assertEqual(values.records[0]["direction"], "upgrade")

    def test_fortios_incomplete_catalog_does_not_claim_no_upgrade(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_COMPLIANCE,
            diagnose_fdsm_image_list=(
                "Last update: 10 secs ago. Total: 104\n"
                "FGT_VM64_KVM-v8.0-FORTINET.out v8.0 b0167 (downgrade)"
            ),
        )

        values = normalize_firmware_recommendation(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertIsNone(values.metrics["firmware_update_available"])
        self.assertFalse(values.metrics["catalog_complete"])

    def test_fortios_api_requires_an_explicit_upgrade_marker(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_COMPLIANCE,
            get_system_status='{"version":"v8.0.1","build":245}',
            system_firmware=(
                '{"available":[{"id":"08000000FIMG0001",'
                '"version":"v8.0.2","build":251,"direction":"upgrade"}]}'
            ),
        )

        values = normalize_firmware_recommendation(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.WARNING)
        self.assertTrue(values.metrics["firmware_update_available"])
        self.assertEqual(values.records[0]["image_id"], "08000000FIMG0001")

    def test_fortios_api_does_not_infer_upgrade_from_an_available_list(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_COMPLIANCE,
            get_system_status='{"version":"v8.0.2","build":251}',
            system_firmware=(
                '{"available":[{"id":"08000000FIMG0001",'
                '"version":"v8.0.3","build":300}]}'
            ),
        )

        values = normalize_firmware_recommendation(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertIsNone(values.metrics["firmware_update_available"])
        self.assertEqual(
            values.reason_code,
            "firmware_catalog_semantics_unverified",
        )

    def test_missing_data_is_parse_failed_not_zero(self):
        raw = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="fortios",
            status="ok",
            data={"cpu": []},
        )
        with self.assertRaises(ParseFailedError):
            normalize_cpu(raw)

    def test_malformed_cisco_output_is_parse_failed(self):
        raw = RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform="cisco-ios",
            status="ok",
            raw_output=fixture("malformed_cpu.txt"),
        )
        with self.assertRaises(ParseFailedError):
            normalize_cpu(raw)

    def test_cisco_ios_facts_include_serial_and_uptime(self):
        raw = RawCollectorResult(
            collector=CollectorId.DEVICE_FACTS,
            platform="cisco-ios",
            status="ok",
            data={
                "show_version": (
                    "Cisco IOS Software, C2800 Software (C2800NM-ADVIPSERVICESK9-M), "
                    "Version 12.4(25d), RELEASE SOFTWARE (fc1)\n"
                    "R1 uptime is 1 year, 2 weeks, 3 days, 4 hours, 5 minutes\n"
                    'System image file is "flash:c2800nm-advipservicesk9-mz.bin"\n'
                    "Processor board ID FTX1234ABCD\n"
                    "Cisco 2811 (revision 53.51) with 249856K/12288K bytes of memory.\n"
                    "Configuration register is 0x2102\n"
                ),
                "show_inventory": (
                    'NAME: "Chassis", DESCR: "Cisco 2811 chassis"\n'
                    "PID: CISCO2811, VID: V05, SN: FTX1234ABCD\n"
                ),
            },
        )

        facts = normalize_device_facts(raw)

        self.assertEqual(facts.hostname, "R1")
        self.assertEqual(facts.serial_number, "FTX1234ABCD")
        self.assertEqual(facts.uptime_seconds, 33_019_500)

    def test_cisco_cbs_facts_include_serial_and_uptime(self):
        raw = RawCollectorResult(
            collector=CollectorId.DEVICE_FACTS,
            platform="cisco-cbs",
            status="ok",
            data={
                "show_system": (
                    "System Description: CBS350-16T-2G\n"
                    "System Up Time (days,hour:min:sec): 12,03:04:05\n"
                    "System Contact:\n"
                    "System Name: SWtento\n"
                    "System Location:\n"
                ),
                "show_version": (
                    "Active-image: flash://system/images/image.bin\n"
                    "  Version: 3.6.0.3\n"
                    "  MD5 Digest: deadbeef\n"
                    "  Date: 02-Aug-2026\n"
                    "  Time: 16:40:41\n"
                ),
                "show_inventory": (
                    'NAME: "1" DESCR: "CBS350-16T-2G"\n'
                    "PID: CBS350-16T-2G VID: V05 SN: FOC2710Y39U\n"
                ),
            },
        )

        facts = normalize_device_facts(raw)

        self.assertEqual(facts.serial_number, "FOC2710Y39U")
        self.assertEqual(facts.uptime_seconds, 1_047_845)

    def test_aruba_aoscx_facts_include_serial_and_uptime(self):
        raw = RawCollectorResult(
            collector=CollectorId.DEVICE_FACTS,
            platform="aruba-aos-cx",
            status="ok",
            data={
                "show_system": (
                    "Hostname : ARUBA-CX-LAB\n"
                    "System Description : ArubaOS-CX\n"
                    "System Contact :\n"
                    "System Location :\n"
                    "Vendor : Aruba\n"
                    "Product Name : JL679A 6100\n"
                    "Chassis Serial Nbr : CN12345678\n"
                    "Base MAC Address : 00:11:22:33:44:55\n"
                    "ArubaOS-CX Version : FL.10.13.1000\n"
                    "Time Zone : UTC\n"
                    "Up Time : 1 week, 2 days, 3 hours, 4 minutes\n"
                ),
                "show_version": "ArubaOS-CX Version FL.10.13.1000\n",
            },
        )

        facts = normalize_device_facts(raw)

        self.assertEqual(facts.serial_number, "CN12345678")
        self.assertEqual(facts.uptime_seconds, 788_640)

    def test_fortios_facts_and_uptime_use_api_payloads(self):
        raw = RawCollectorResult(
            collector=CollectorId.DEVICE_FACTS,
            platform="fortios",
            status="ok",
            data={
                "hostname": "forti-mt-2",
                "model": "VM64",
                "os_version": "v7.6.4",
                "serial_number": "FGVMEV1234567890",
                "uptime_state": {
                    "results": {
                        "snapshot_utc_time": 1_700_100_000_000,
                        "utc_last_reboot": 1_700_000_000_000,
                    }
                },
            },
        )

        facts = normalize_device_facts(raw)
        uptime = normalize_uptime(raw)

        self.assertEqual(facts.serial_number, "FGVMEV1234567890")
        self.assertEqual(facts.uptime_seconds, 100_000)
        self.assertEqual(uptime.uptime_seconds, 100_000)

    def test_ios_resource_parsers_keep_thresholds_explicit(self):
        raw = self.command_result(
            "cisco-ios",
            CollectorId.IOS_RESOURCES,
            show_processes_memory_sorted=(
                "Processor Pool Total: 1000 Used: 250 Free: 750\n"
            ),
            show_environment_all="SYSTEM TEMPERATURE STATUS: Normal\nFan 1: OK",
        )

        memory = normalize_memory(raw)
        environment = normalize_environment_health(raw)

        self.assertEqual(memory.value, 25.0)
        self.assertEqual(memory.outcome, EvaluationOutcome.UNKNOWN)
        self.assertEqual(memory.reason_code, "memory_threshold_unconfigured")
        self.assertEqual(environment.outcome, EvaluationOutcome.PASS)

    def test_cbs_environment_failure_is_not_reported_as_healthy(self):
        raw = self.command_result(
            "cisco-cbs250-350",
            CollectorId.CBS_RESOURCES,
            show_environment_all="fans Failure\nTemperature is OK",
        )

        values = normalize_environment_health(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.FAIL)
        self.assertEqual(values.reason_code, "environment_fault_detected")

    def test_absolute_interface_errors_require_a_delta(self):
        raw = self.command_result(
            "cisco-ios",
            CollectorId.IOS_INTERFACES,
            show_interfaces=(
                "GigabitEthernet1 is up, line protocol is up\n"
                "  12 input errors, 3 CRC, 0 frame, 0 overrun, 0 ignored\n"
            ),
        )

        values = normalize_interface_errors(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertEqual(values.reason_code, "counter_delta_unavailable")
        self.assertEqual(values.metrics["absolute_error_total"], 15)

    def test_fortios_physical_interfaces_and_spaced_error_counters(self):
        raw = RawCollectorResult(
            collector=CollectorId.FORTIOS_INTERFACES,
            platform="fortios",
            status="ok",
            data={
                "get_system_interface_physical": (
                    "== [onboard]\n"
                    "        ==[port1]\n"
                    "                status: up\n"
                    "        ==[port2]\n"
                    "                status: down\n"
                    "        ==[port3]\n"
                    "                status: up\n"
                ),
                "diagnose_hardware_deviceinfo_nic": (
                    "===== diagnose hardware deviceinfo nic port1 =====\n"
                    "Link: up\nRx errors: 0\nTx errors: 0\n"
                    "===== diagnose hardware deviceinfo nic port2 =====\n"
                    "Link: down\nRx errors: 0\nTx errors: 0\n"
                    "===== diagnose hardware deviceinfo nic port3 =====\n"
                    "Link: up\nRx errors: 0\nTx errors: 0\n"
                ),
                "discovered_interface_count": 3,
                "interface_diagnostics_truncated": False,
            },
        )

        state = normalize_interface_state(raw)
        errors = normalize_interface_errors(raw)

        self.assertEqual(state.metrics["discovered_physical_interfaces"], 3)
        self.assertEqual(state.metrics["up"], 2)
        self.assertEqual(state.metrics["down_or_unused"], 1)
        self.assertEqual(errors.reason_code, "counter_delta_unavailable")
        self.assertEqual(errors.metrics["absolute_error_total"], 0)

    def test_fortios_storage_inventory_is_normalized_without_threshold(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_RESOURCES,
            diagnose_hardware_deviceinfo_disk=(
                "Disk SYSTEM(boot) 2.0GiB type: IDE [Virtio Disk]\n"
                "  partition 479.0MiB, 289.0MiB free mounted: Y\n"
                "  partition 1.4GiB, 1.3GiB free mounted: Y\n"
                "Disk Virtual-Disk ref: 16 30.0GiB type: IDE [Virtio Disk]\n"
                "  partition ref: 17 29.4GiB, 29.4GiB free mounted: Y\n"
                "Total available disks: 2\n"
                "Max SSD disks: 8 Available storage disks: 1\n"
            ),
        )

        values = normalize_storage_health(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertEqual(values.reason_code, "storage_threshold_unconfigured")
        self.assertEqual(values.metrics["disk_count"], 2)
        self.assertEqual(values.metrics["available_storage_disk_count"], 1)

    def test_fortios_important_log_severities_include_event_reason(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_OPERATIONS,
            execute_log_display=(
                'date=2026-10-24 time=20:00:01 level="alert" '
                'logdesc="Configuration changed" '
                'msg="Administrator changed system settings"\n'
                'date=2026-10-24 time=20:01:02 level="warning" '
                'logdesc="Update failed" msg="FortiGuard timeout"\n'
                'date=2026-10-24 time=20:02:03 level="information" '
                'logdesc="Admin login successful"\n'
            ),
        )

        values = normalize_recent_logs(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.WARNING)
        self.assertEqual(values.reason_code, "important_log_events_detected")
        self.assertEqual(values.metrics["important_event_count"], 2)
        self.assertIn("Configuration changed", values.affected_items[0])
        self.assertIn("Administrator changed", values.affected_items[0])

    def test_fortios_ha_members_sync_roles_and_heartbeat_are_structured(self):
        raw = self.command_result(
            "fortios",
            CollectorId.FORTIOS_OPERATIONS,
            get_system_ha_status=(
                "HA Health Status: OK\n"
                "Model: FortiGate-VM64-KVM\n"
                "Mode: HA A-P\n"
                "Group Name: GNS3-HA\n"
                "Group ID: 10\n"
                "Cluster Uptime: 0 days 3h:41m:18s\n"
                "Configuration Status:\n"
                "  SERIAL-A(updated 3 seconds ago): in-sync\n"
                "  SERIAL-A chksum dump: 18 ba 83 0d\n"
                "  SERIAL-B(updated 1 seconds ago): in-sync\n"
                "  SERIAL-B chksum dump: 18 ba 83 0d\n"
                "System Usage stats:\n"
                "  SERIAL-A(updated 3 seconds ago):\n"
                "    sessions=6, average-cpu-user/nice/system/idle="
                "0%/0%/0%/92%, memory=53%\n"
                "  SERIAL-B(updated 1 seconds ago):\n"
                "    sessions=0, average-cpu-user/nice/system/idle="
                "0%/0%/0%/91%, memory=50%\n"
                "HBDEV stats:\n"
                "  SERIAL-A(updated 3 seconds ago):\n"
                "    port3: physical/1000auto, up, "
                "rx-bytes/packets/dropped/errors=100/10/0/0, "
                "tx=200/20/0/0\n"
                "  SERIAL-B(updated 1 seconds ago):\n"
                "    port3: physical/1000auto, up, "
                "rx-bytes/packets/dropped/errors=200/20/0/0, "
                "tx=100/10/0/0\n"
                "number of member: 2\n"
                "FGT-01, SERIAL-A, HA cluster index = 0\n"
                "FGT-02, SERIAL-B, HA cluster index = 1\n"
                "number of vcluster: 1\n"
                "Primary: SERIAL-A, HA operating index = 0\n"
                "Secondary: SERIAL-B, HA operating index = 1\n"
            ),
        )

        values = normalize_ha_status(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.PASS)
        self.assertEqual(values.metrics["mode"], "HA A-P")
        self.assertEqual(values.metrics["group_name"], "GNS3-HA")
        self.assertEqual(values.metrics["declared_member_count"], 2)
        self.assertEqual(values.metrics["in_sync_member_count"], 2)
        self.assertTrue(values.metrics["checksum_match"])
        self.assertEqual(len(values.records), 2)
        self.assertEqual(values.records[0]["hostname"], "FGT-01")
        self.assertEqual(values.records[0]["role"], "primary")
        self.assertEqual(values.records[1]["role"], "secondary")
        self.assertTrue(values.records[0]["heartbeat_all_up"])

    def test_license_certificate_ntp_and_logs_have_actionable_outcomes(self):
        compliance = self.command_result(
            "cisco-ios",
            CollectorId.IOS_COMPLIANCE,
            show_license_summary="Authorization Status: OUT OF COMPLIANCE",
            show_crypto_pki_certificates="end date: Jan 01 00:00:00 2020 UTC",
        )
        operations = self.command_result(
            "cisco-ios",
            CollectorId.IOS_OPERATIONS,
            show_ntp_status="Clock is unsynchronized, stratum 16",
            show_logging="%LINK-3-UPDOWN: Interface Gi1 changed state to down",
        )

        self.assertEqual(
            normalize_license_state(compliance).outcome,
            EvaluationOutcome.FAIL,
        )
        self.assertEqual(
            normalize_pki_certificates(compliance).outcome,
            EvaluationOutcome.FAIL,
        )
        self.assertEqual(
            normalize_ntp_status(operations).outcome,
            EvaluationOutcome.FAIL,
        )
        self.assertEqual(
            normalize_recent_logs(operations).outcome,
            EvaluationOutcome.FAIL,
        )


if __name__ == "__main__":
    unittest.main()
