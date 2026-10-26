import unittest
from datetime import datetime, timezone
from uuid import uuid4

from atlas.prophylaxis.models import (
    CheckId,
    CollectionStatus,
    CPUUtilizationValues,
    DeviceFactsValues,
    DiagnosticValues,
    EvaluationOutcome,
    SystemMetricValues,
)
from atlas.prophylaxis.run_models import (
    ProphylaxisCheckResult,
    ProphylaxisRun,
    RunSummary,
)
from atlas.prophylaxis.run_store import FileRunStore


def result(**overrides):
    values = {
        "check": CheckId.CPU_UTILIZATION.value,
        "device_id": 42,
        "device_name": "R1",
        "management_ip": "192.0.2.42",
        "platform": "cisco-ios",
        "status": "success",
        "collection_status": CollectionStatus.COLLECTED,
        "evaluation": EvaluationOutcome.PASS,
        "values": CPUUtilizationValues(current_percent=12),
    }
    values.update(overrides)
    return ProphylaxisCheckResult(**values)


class ProphylaxisReportTests(unittest.TestCase):
    def test_report_data_is_compact_and_grouped_per_device(self):
        run = ProphylaxisRun(
            run_id=uuid4(),
            status="PARTIAL",
            report_path="/run/report.txt",
            summary=RunSummary(total=1, warning=1),
            results=(
                result(
                    check=CheckId.HA_STATUS.value,
                    platform="fortios",
                    evaluation=EvaluationOutcome.WARNING,
                    values=DiagnosticValues(
                        outcome=EvaluationOutcome.WARNING,
                        reason_code="ha_heartbeat_interface_down",
                        summary="One heartbeat interface is down",
                        metrics={"declared_member_count": 2},
                        records=({
                            "record_type": "ha_member",
                            "hostname": "FGT-01",
                            "role": "primary",
                        },),
                        affected_items=("FGT-01 heartbeat=down",),
                        collected_at=datetime.now(timezone.utc),
                        context={"transport": "ssh", "port": 22},
                        source_commands=("get system ha status",),
                        evaluation_rule="ha_heartbeat_interface_down",
                        recommended_action="Review HA heartbeat",
                    ),
                ),
            ),
        )

        payload = FileRunStore._report_data(run)
        check = payload["devices"][0]["checks"][0]

        self.assertEqual(len(payload["devices"]), 1)
        self.assertEqual(payload["devices"][0]["device"]["name"], "R1")
        self.assertEqual(check["evaluation"], "WARNING")
        self.assertEqual(check["records"][0]["hostname"], "FGT-01")
        self.assertEqual(check["findings"], ["FGT-01 heartbeat=down"])
        self.assertNotIn("collected_at", check)
        self.assertNotIn("context", check)
        self.assertNotIn("source_commands", check)

    def test_report_groups_device_and_separates_collection_from_evaluation(self):
        run = ProphylaxisRun(
            run_id=uuid4(),
            status="PARTIAL",
            report_path="/run/report.txt",
            summary=RunSummary(
                total=6,
                success=2,
                warning=1,
                unknown=1,
                failure=1,
                skipped=1,
            ),
            results=(
                result(),
                result(
                    check=CheckId.DEVICE_FACTS.value,
                    values=None,
                    facts=DeviceFactsValues(
                        hostname="R1",
                        model="ISR4331",
                        os_version="15.5",
                    ),
                ),
                result(
                    check=CheckId.LICENSE_STATE.value,
                    evaluation=EvaluationOutcome.WARNING,
                    values=SystemMetricValues(
                        outcome=EvaluationOutcome.WARNING,
                        reason_code="renewal_should_be_planned",
                    ),
                ),
                result(
                    check=CheckId.NTP_SYNC.value,
                    status="skipped",
                    collection_status=CollectionStatus.UNSUPPORTED,
                    evaluation=EvaluationOutcome.UNSUPPORTED,
                    values=None,
                    error="unsupported_check",
                ),
                result(
                    check=CheckId.ROUTING_TABLE.value,
                    status="incomplete",
                    evaluation=EvaluationOutcome.UNKNOWN,
                    values=SystemMetricValues(
                        outcome=EvaluationOutcome.UNKNOWN,
                        reason_code="automatic_evaluation_not_configured",
                    ),
                ),
                result(
                    check=CheckId.INTERFACE_ERROR_COUNTERS.value,
                    status="failed",
                    collection_status=CollectionStatus.FAILED,
                    evaluation=EvaluationOutcome.FAIL,
                    values=None,
                    error="connection_failed",
                ),
            ),
        )

        report = FileRunStore._report(run)

        self.assertEqual(report.count("Zariadenie: R1"), 1)
        for section in (
            "Vyžaduje zásah",
            "Naplánovať",
            "Zdravie",
            "Inventár",
            "Nepodporované/neoverené",
        ):
            self.assertIn(section, report)
        self.assertIn("collection=COLLECTED, evaluation=WARNING", report)
        self.assertIn("Unknown: 1", report)
        self.assertIn("collection=COLLECTED, evaluation=UNKNOWN", report)
        self.assertIn("collection=UNSUPPORTED, evaluation=UNSUPPORTED", report)


if __name__ == "__main__":
    unittest.main()
