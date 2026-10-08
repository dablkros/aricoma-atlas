import unittest
from pathlib import Path

from atlas.prophylaxis.errors import ParseFailedError
from atlas.prophylaxis.models import CheckId, RawCheckResult
from atlas.prophylaxis.parsers import normalize_cpu


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

        self.assertEqual(normalize_cpu(ios).current_percent, 8.0)
        self.assertEqual(normalize_cpu(cbs).five_minute_percent, 3.0)

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


if __name__ == "__main__":
    unittest.main()
