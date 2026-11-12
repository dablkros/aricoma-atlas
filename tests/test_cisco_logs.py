import unittest
from datetime import datetime, timezone

from atlas.prophylaxis.cisco_logs import enrich_cisco_log_results
from atlas.prophylaxis.cisco_raw import CiscoRawCommandResult


def result(**updates):
    payload = {
        "command": "show logging",
        "status": "success",
        "duration_ms": 10,
        "started_at": datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 11, 12, 10, 0, 1, tzinfo=timezone.utc),
        "stdout": "stored raw output",
        "parser_status": "parsed",
        "parsing_status": "success",
        "command_id": "log_events",
        "category": "logs",
        "parser_engine": "ntc_textfsm",
        "structured_data": [],
    }
    payload.update(updates)
    return CiscoRawCommandResult.model_validate(payload)


class CiscoLogWindowTests(unittest.TestCase):
    def clock(self):
        return result(
            command="show clock detail", command_id="device_clock", category="time",
            structured_data=[{
                "time": "10:00:00.000", "timezone": "UTC", "dayweek": "Thu",
                "month": "Nov", "day": "12", "year": "2026",
            }],
        )

    def test_filters_strict_seven_day_window_and_marks_partial_coverage(self):
        logs = result(structured_data=[
            {"month": "Nov", "day": "11", "time": "09:00:00.000", "timezone": "UTC", "facility": "LINK", "severity": "3", "mnemonic": "UPDOWN", "message": "recent"},
            {"month": "Nov", "day": "01", "time": "09:00:00.000", "timezone": "UTC", "facility": "SYS", "severity": "5", "mnemonic": "CONFIG", "message": "old"},
            {"month": "", "day": "", "time": "", "timezone": "", "facility": "SYS", "severity": "5", "mnemonic": "UPTIME", "message": "undated"},
        ])
        enriched = enrich_cisco_log_results((self.clock(), logs))[1]
        normalized = enriched.normalized_data
        self.assertEqual(1, normalized["filtered_events"])
        self.assertEqual(1, normalized["undated_events"])
        self.assertEqual("partial", normalized["coverage_status"])
        self.assertEqual("recent", normalized["events"][0]["message"])

    def test_yearless_december_event_rolls_back_from_january_reference(self):
        clock = self.clock().model_copy(update={"structured_data": [{
            "time": "10:00:00.000", "timezone": "UTC", "dayweek": "Fri",
            "month": "Jan", "day": "02", "year": "2026",
        }]})
        logs = result(structured_data=[{
            "month": "Dec", "day": "31", "time": "23:00:00.000", "timezone": "UTC",
            "facility": "SYS", "severity": "5", "mnemonic": "RESTART", "message": "previous year",
        }])
        normalized = enrich_cisco_log_results((clock, logs))[1].normalized_data
        self.assertTrue(normalized["events"][0]["timestamp"].startswith("2025-12-31"))

    def test_unknown_timezone_keeps_events_undated(self):
        clock = self.clock().model_copy(update={"structured_data": [{
            "time": "10:00:00.000", "timezone": "CET", "dayweek": "Thu",
            "month": "Nov", "day": "12", "year": "2026",
        }]})
        logs = result(structured_data=[{
            "month": "Nov", "day": "11", "time": "09:00:00.000", "timezone": "",
            "facility": "SYS", "severity": "5", "mnemonic": "CONFIG", "message": "ambiguous zone",
        }])
        normalized = enrich_cisco_log_results((clock, logs))[1].normalized_data
        self.assertEqual("unknown", normalized["coverage_status"])
        self.assertEqual(1, normalized["undated_events"])


if __name__ == "__main__":
    unittest.main()
