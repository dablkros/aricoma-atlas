import unittest
from types import SimpleNamespace
from uuid import uuid4

from atlas.prophylaxis.evaluation import (
    UPTIME_TOO_LONG_SECONDS,
    UPTIME_TOO_SHORT_SECONDS,
    evaluate_uptime,
)
from atlas.prophylaxis.models import (
    CheckId,
    CollectionStatus,
    DiagnosticValues,
    EvaluationOutcome,
    UptimeValues,
)
from atlas.prophylaxis.run_models import ProphylaxisRun
from atlas.prophylaxis.run_store import FileRunStore
from atlas.prophylaxis.runs import DeviceFactsRunService


class UptimeEvaluationTests(unittest.TestCase):
    def test_uptime_boundaries_are_vendor_neutral(self):
        cases = (
            (
                UPTIME_TOO_SHORT_SECONDS - 1,
                EvaluationOutcome.WARNING,
                "uptime_too_short",
            ),
            (UPTIME_TOO_SHORT_SECONDS, EvaluationOutcome.PASS, None),
            (UPTIME_TOO_LONG_SECONDS, EvaluationOutcome.PASS, None),
            (
                UPTIME_TOO_LONG_SECONDS + 1,
                EvaluationOutcome.WARNING,
                "uptime_too_long",
            ),
        )
        for seconds, expected_outcome, expected_reason in cases:
            with self.subTest(seconds=seconds):
                outcome, reason = evaluate_uptime(
                    UptimeValues(uptime_seconds=seconds)
                )
                self.assertEqual(outcome, expected_outcome)
                self.assertEqual(reason, expected_reason)

    def test_uptime_warning_is_preserved_in_run_result(self):
        result = DeviceFactsRunService._success_result(
            6,
            "SWtento",
            "10.255.253.10",
            "cisco-cbs250-350",
            CheckId.UPTIME,
            UptimeValues(uptime_seconds=UPTIME_TOO_LONG_SECONDS + 1),
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.collection_status, CollectionStatus.COLLECTED)
        self.assertEqual(result.evaluation, EvaluationOutcome.WARNING)
        self.assertEqual(result.values.reason_code, "uptime_too_long")

    def test_fortios_uptime_is_collected_without_threshold_evaluation(self):
        result = DeviceFactsRunService._success_result(
            7,
            "FGT-01",
            "10.99.98.104",
            "fortios",
            CheckId.UPTIME,
            UptimeValues(uptime_seconds=UPTIME_TOO_SHORT_SECONDS - 1),
        )
        summary = DeviceFactsRunService._summary((result,))

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.collection_status, CollectionStatus.COLLECTED)
        self.assertEqual(result.evaluation, EvaluationOutcome.UNKNOWN)
        self.assertEqual(
            result.values.reason_code,
            "automatic_evaluation_not_configured",
        )
        self.assertEqual(summary.unknown, 1)
        self.assertEqual(summary.failure, 0)

    def test_fortios_neighbors_preserve_evidence_without_policy(self):
        raw = SimpleNamespace(
            context={"transport": "ssh", "port": 22, "vdom": "root"}
        )
        cases = (
            (CheckId.ROUTING_PROTOCOL_NEIGHBORS, EvaluationOutcome.WARNING),
        )
        for check, collected_outcome in cases:
            with self.subTest(check=check.value):
                result = DeviceFactsRunService._annotate_fortios_result(
                    "fortios",
                    check,
                    DiagnosticValues(
                        outcome=collected_outcome,
                        reason_code="collected_state",
                        summary="Collected evidence",
                        metrics={"finding_count": 1},
                        affected_items=("device evidence",),
                    ),
                    raw,
                )

                self.assertEqual(result.outcome, EvaluationOutcome.UNKNOWN)
                self.assertEqual(
                    result.reason_code,
                    "automatic_evaluation_not_configured",
                )
                self.assertEqual(result.metrics["finding_count"], 1)
                self.assertEqual(result.affected_items, ("device evidence",))

    def test_short_uptime_warning_is_visible_in_report(self):
        result = DeviceFactsRunService._success_result(
            6,
            "SWtento",
            "10.255.253.10",
            "cisco-cbs250-350",
            CheckId.UPTIME,
            UptimeValues(uptime_seconds=UPTIME_TOO_SHORT_SECONDS - 1),
        )
        run = ProphylaxisRun(
            run_id=uuid4(),
            status="PARTIAL",
            report_path="/run/report.txt",
            summary=DeviceFactsRunService._summary((result,)),
            results=(result,),
        )

        report = FileRunStore._report(run)

        self.assertEqual(run.summary.warning, 1)
        self.assertIn("Naplánovať", report)
        self.assertIn("uptime_too_short", report)
        self.assertIn("uptime=2 dni 23 hodín 59 minút 59 sekúnd", report)


if __name__ == "__main__":
    unittest.main()
