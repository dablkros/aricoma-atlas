import unittest
from uuid import uuid4

from atlas.prophylaxis.models import (
    CheckId,
    CollectionStatus,
    CPUUtilizationValues,
    DeviceFactsValues,
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
    def test_report_groups_device_and_separates_collection_from_evaluation(self):
        run = ProphylaxisRun(
            run_id=uuid4(),
            status="PARTIAL",
            report_path="/run/report.txt",
            summary=RunSummary(
                total=5,
                success=2,
                warning=1,
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
        self.assertIn("collection=UNSUPPORTED, evaluation=UNSUPPORTED", report)


if __name__ == "__main__":
    unittest.main()
