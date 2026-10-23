import unittest
from unittest.mock import Mock

from atlas.device_credentials import DeviceValidationError
from atlas.integrations.cisco_support import (
    CiscoEoxRecord,
    CiscoEoxResult,
    CiscoSoftwareSuggestion,
)
from atlas.prophylaxis.collectors import CollectorRegistry
from atlas.prophylaxis.models import (
    CheckId,
    CollectorId,
    EolStatusValues,
    EvaluationOutcome,
    FirmwareRecommendationValues,
    RawCollectorResult,
    expand_check_selection,
)
from atlas.prophylaxis.parsers import (
    normalize_snmp_cpu,
    normalize_snmp_fans,
    normalize_snmp_memory,
    normalize_snmp_temperature,
)
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry
from atlas.prophylaxis.runs import DeviceFactsRunService


def cbs_raw(model="CBS350-16T-2G", fan_status=5):
    metrics = {
        "cpu_one_second": {"status": "ok", "value": 30},
        "cpu_one_minute": {"status": "ok", "value": 20},
        "cpu_five_minutes": {"status": "ok", "value": 94},
        "temperature": {"status": "ok", "value": 47},
    }
    metrics.update({
        f"fan_{index}": {"status": "ok", "value": fan_status}
        for index in range(1, 4)
    })
    return RawCollectorResult(
        collector=CollectorId.SNMP_SYSTEM,
        platform="cisco-cbs",
        status="ok",
        data={
            "model": model,
            "sys_object_id": "1.3.6.1.4.1.9.6.1.1004.18.16",
            "metrics": metrics,
        },
    )


class SystemMetricTests(unittest.TestCase):
    def test_cisco_recommendation_compares_both_release_formats(self):
        cisco = Mock()
        cisco.software_suggestions.return_value = (
            CiscoSoftwareSuggestion(
                product_id="CISCO2811",
                software_type="IOS Software",
                release="15.1.4M5",
                alternate_release="15.1(4)M5",
                release_date="17 Nov 2014",
                lifecycle="MD",
                suggested=True,
            ),
        )
        service = DeviceFactsRunService(
            netbox=None,
            openbao=None,
            backend=None,
            store=None,
            concurrency=1,
            cisco_support=cisco,
        )

        values = service._firmware_recommendation_values(
            "CISCO2811",
            "15.1(4)M5",
        )

        self.assertIsInstance(values, FirmwareRecommendationValues)
        self.assertEqual(values.outcome, EvaluationOutcome.PASS)
        self.assertEqual(values.reason_code, "installed_release_suggested")

    def test_cisco_eox_uses_serial_and_software_release_independently(self):
        cisco = Mock()
        cisco.hardware_eox.return_value = CiscoEoxResult((
            CiscoEoxRecord(
                product_id="CISCO2811",
                description="Integrated Services Router",
                bulletin_number="EOL0001",
                bulletin_url="https://www.cisco.com/example",
                announcement_date="2010-01-01",
                end_of_sale_date="2011-01-01",
                end_of_software_maintenance_date="2012-01-01",
                end_of_security_support_date="2013-01-01",
                last_date_of_support="2016-01-01",
                updated_date="2010-01-02",
            ),
        ))
        cisco.software_eox.return_value = CiscoEoxResult(())
        service = DeviceFactsRunService(
            netbox=None,
            openbao=None,
            backend=None,
            store=None,
            concurrency=1,
            cisco_support=cisco,
        )

        values = service._eol_status_values(
            serial_number="FTX1234ABCD",
            product_id="CISCO2811",
            installed_release="15.1(4)M5",
            platform="cisco-ios",
        )

        self.assertIsInstance(values, EolStatusValues)
        self.assertEqual(values.outcome, EvaluationOutcome.FAIL)
        self.assertEqual(values.hardware_state, "record_found")
        self.assertEqual(values.software_state, "no_record")
        self.assertEqual(values.hardware_record_count, 1)
        self.assertEqual(values.software_record_count, 0)
        self.assertFalse(values.records_truncated)
        cisco.hardware_eox.assert_called_once_with("FTX1234ABCD")
        cisco.software_eox.assert_called_once_with("15.1(4)M5", "IOS")

    def test_oxidized_backup_status_is_reused_without_starting_a_backup(self):
        service = DeviceFactsRunService(
            netbox=None,
            openbao=None,
            backend=None,
            store=None,
            concurrency=1,
        )
        service._oxidized_nodes = {
            "SWtento": ({
                "name": "SWtento",
                "last": {
                    "status": "success",
                    "end": "2026-10-20T08:30:00Z",
                },
            },),
        }

        values = service._configuration_backup_values("SWtento")
        missing = service._configuration_backup_values("missing")

        self.assertEqual(values.outcome, EvaluationOutcome.PASS)
        self.assertFalse(values.metrics["manual_backup_required"])
        self.assertEqual(missing.outcome, EvaluationOutcome.WARNING)
        self.assertTrue(missing.metrics["manual_backup_required"])

    def test_cbs_cpu_matches_verified_three_oid_contract(self):
        values = normalize_snmp_cpu(cbs_raw())
        self.assertEqual(values.current_percent, 30)
        self.assertEqual(values.one_minute_percent, 20)
        self.assertEqual(values.five_minute_percent, 94)

    def test_temperature_keeps_raw_value_without_inventing_unit(self):
        values = normalize_snmp_temperature(cbs_raw())
        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertEqual(values.value, 47)
        self.assertIsNone(values.unit)
        self.assertEqual(values.reason_code, "temperature_unit_unverified")

    def test_cbs350_16t_not_present_fans_are_not_applicable(self):
        values = normalize_snmp_fans(cbs_raw())
        self.assertEqual(values.outcome, EvaluationOutcome.NOT_APPLICABLE)
        self.assertEqual(values.expected_components, 0)
        self.assertTrue(all(
            sensor.outcome == EvaluationOutcome.NOT_APPLICABLE
            for sensor in values.sensors
        ))

    def test_unknown_model_not_present_fans_remain_unknown(self):
        values = normalize_snmp_fans(cbs_raw(model="UNKNOWN-CBS"))
        self.assertEqual(values.outcome, EvaluationOutcome.UNKNOWN)
        self.assertIsNone(values.expected_components)

    def test_cbs350_12xt_requires_two_healthy_fans(self):
        raw = cbs_raw(model="CBS350-12XT", fan_status=1)
        raw.data["metrics"]["fan_3"]["value"] = 5

        values = normalize_snmp_fans(raw)

        self.assertEqual(values.outcome, EvaluationOutcome.PASS)
        self.assertEqual(values.expected_components, 2)
        self.assertEqual(
            [sensor.outcome for sensor in values.sensors],
            [
                EvaluationOutcome.PASS,
                EvaluationOutcome.PASS,
                EvaluationOutcome.NOT_APPLICABLE,
            ],
        )

        raw.data["metrics"]["fan_2"]["value"] = 5
        failed = normalize_snmp_fans(raw)
        self.assertEqual(failed.outcome, EvaluationOutcome.FAIL)
        self.assertEqual(failed.reason_code, "fan_failure_detected")

    def test_memory_is_explicitly_unsupported_until_oid_is_verified(self):
        values = normalize_snmp_memory(cbs_raw())
        self.assertEqual(values.outcome, EvaluationOutcome.UNSUPPORTED)
        self.assertEqual(values.reason_code, "cbs350_memory_oid_unverified")

    def test_aruba_metrics_are_not_fabricated(self):
        raw = RawCollectorResult(
            collector=CollectorId.SNMP_SYSTEM,
            platform="aruba-aos-cx",
            status="ok",
            data={
                "model": "ABC123 ArubaOS-CX_OVA",
                "sys_object_id": "1.3.6.1.4.1.47196.4.1.1.1.1",
                "metrics": {
                    "cpu_one_second": {"status": "no_such_object"},
                    "memory": {"status": "no_such_object"},
                },
            },
        )
        self.assertEqual(
            normalize_snmp_cpu(raw).outcome,
            EvaluationOutcome.UNSUPPORTED,
        )
        self.assertEqual(
            normalize_snmp_memory(raw).outcome,
            EvaluationOutcome.UNSUPPORTED,
        )

    def test_cbs_groups_share_identity_and_resource_collectors(self):
        for platform in ("cisco-cbs", "cisco-cbs250-350"):
            with self.subTest(platform=platform):
                profile = AnsiblePlatformRegistry().resolve(platform)
                checks, unknown = expand_check_selection(
                    {"identity", "resources"}
                )
                plan = CollectorRegistry().plan(checks, profile)

                self.assertEqual(unknown, ())
                self.assertEqual(
                    [task.collector.id for task in plan.tasks],
                    [
                        CollectorId.DEVICE_FACTS,
                        CollectorId.SNMP_SYSTEM,
                        CollectorId.CBS_RESOURCES,
                    ],
                )
                self.assertEqual(
                    plan.tasks[0].checks,
                    (
                        CheckId.DEVICE_FACTS,
                        CheckId.OS_VERSION,
                        CheckId.UPTIME,
                    ),
                )
                self.assertEqual(
                    plan.tasks[1].checks,
                    (
                        CheckId.CPU_UTILIZATION,
                        CheckId.CPU_TEMPERATURE,
                        CheckId.FAN_HEALTH,
                    ),
                )
                self.assertEqual(
                    plan.tasks[2].checks,
                    (
                        CheckId.MEMORY_UTILIZATION,
                        CheckId.ENVIRONMENT_HEALTH,
                    ),
                )
                self.assertEqual(
                    plan.unsupported_checks,
                    (),
                )

    def test_cisco_ios_plan_groups_only_verified_thematic_collectors(self):
        profile = AnsiblePlatformRegistry().resolve("cisco-ios")
        checks, unknown = expand_check_selection(
            {
                "identity",
                "resources",
                "interfaces",
                "compliance",
                "operations",
            }
        )
        plan = CollectorRegistry().plan(checks, profile)

        self.assertEqual(unknown, ())
        self.assertEqual(
            [task.collector.id for task in plan.tasks],
            [
                CollectorId.IOS_IDENTITY,
                CollectorId.IOS_RESOURCES,
                CollectorId.IOS_INTERFACES,
                CollectorId.IOS_COMPLIANCE,
                CollectorId.IOS_OPERATIONS,
            ],
        )
        self.assertEqual(
            plan.tasks[0].checks,
            (CheckId.DEVICE_FACTS, CheckId.OS_VERSION, CheckId.UPTIME),
        )
        self.assertEqual(
            plan.unsupported_checks,
            (
                CheckId.FIRMWARE_RECOMMENDATION,
                CheckId.EOL_STATUS,
                CheckId.CONFIGURATION_BACKUP,
            ),
        )

    def test_evaluation_controls_run_status_without_losing_values(self):
        result = DeviceFactsRunService._success_result(
            6,
            "SWtento",
            "10.255.253.10",
            "cisco-cbs",
            CheckId.FAN_HEALTH,
            normalize_snmp_fans(cbs_raw()),
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.evaluation, EvaluationOutcome.NOT_APPLICABLE)
        self.assertIsNotNone(result.values)

    def test_run_uses_one_device_specific_snmp_secret_and_one_batch(self):
        class NetBox:
            def get_device_services(self, *_args):
                return []

        class OpenBao:
            def __init__(self):
                self.paths = []

            def get_snmp_credentials(self, vendor, profile):
                self.paths.append(f"devices/credentials/{vendor}/{profile}/snmp")
                return ({
                    "version": "2c",
                    "community": "fixtureCommunity",
                }, 1)

        class Backend:
            def __init__(self):
                self.calls = []

            def execute(self, **kwargs):
                self.calls.append(kwargs)
                return cbs_raw()

        openbao = OpenBao()
        backend = Backend()
        service = DeviceFactsRunService(
            NetBox(),
            openbao,
            backend=backend,
            store=None,
            concurrency=1,
        )
        results = service._collect({
            "id": 6,
            "name": "SWtento",
            "primary_ip4": {"address": "10.255.253.10/24"},
            "platform": {"slug": "cisco-cbs"},
            "device_type": {
                "model": "CBS350-16T-2G",
                "manufacturer": {"slug": "cisco"},
            },
            "custom_fields": {
                "credential_profile": "default",
                "profylaxia_checks": [
                    "cpu_utilization",
                    "cpu_temperature",
                    "fan_health",
                ],
            },
        })

        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0]["collector_id"], CollectorId.SNMP_SYSTEM)
        self.assertEqual(backend.calls[0]["port"], 161)
        self.assertEqual(backend.calls[0]["credentials"]["community"], "fixtureCommunity")
        self.assertEqual(
            openbao.paths,
            ["devices/credentials/cisco/SWtento/snmp"],
        )
        self.assertEqual(
            {result.evaluation for result in results},
            {
                EvaluationOutcome.PASS,
                EvaluationOutcome.UNKNOWN,
                EvaluationOutcome.NOT_APPLICABLE,
            },
        )

    def test_run_falls_back_only_to_explicit_snmp_profile_when_device_leaf_missing(self):
        class NetBox:
            def get_device_services(self, *_args):
                return []

        class OpenBao:
            def __init__(self):
                self.profiles = []

            def get_snmp_credentials(self, _vendor, profile):
                self.profiles.append(profile)
                if profile == "SWtento":
                    raise DeviceValidationError(
                        "missing",
                        code="snmp_credential_not_found",
                        category="error",
                    )
                return ({"version": "2c", "community": "profileSecret"}, 1)

        class Backend:
            def execute(self, **_kwargs):
                return cbs_raw()

        openbao = OpenBao()
        service = DeviceFactsRunService(
            NetBox(), openbao, backend=Backend(), store=None, concurrency=1,
        )
        results = service._collect({
            "id": 6,
            "name": "SWtento",
            "primary_ip4": {"address": "10.255.253.10/24"},
            "platform": {"slug": "cisco-cbs"},
            "device_type": {
                "model": "CBS350-16T-2G",
                "manufacturer": {"slug": "cisco"},
            },
            "custom_fields": {
                "credential_profile": "branch-default",
                "profylaxia_checks": ["cpu_utilization"],
            },
        })

        self.assertEqual(openbao.profiles, ["SWtento", "branch-default"])
        self.assertEqual(results[0].evaluation, EvaluationOutcome.PASS)


if __name__ == "__main__":
    unittest.main()
