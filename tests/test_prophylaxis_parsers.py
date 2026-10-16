import unittest
from pathlib import Path

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import (
    CheckId,
    CollectorId,
    RawCheckResult,
    RawCollectorResult,
)
from atlas.prophylaxis.parsers import (
    normalize_cpu,
    normalize_device_facts,
    normalize_uptime,
)


FIXTURES = Path(__file__).parent / "fixtures/prophylaxis"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ProphylaxisParserTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
