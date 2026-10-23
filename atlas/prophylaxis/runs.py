"""Asynchronous orchestration for NetBox-selected Profylaxia groups."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import logging
import threading

from atlas.device_credentials import DeviceValidationError
from atlas.integrations.cisco_support import CiscoEoxRecord, CiscoSupportAPIError
from atlas.prophylaxis.collectors import (
    CollectionPlan,
    CollectorRegistry,
    CollectorTask,
)
from atlas.prophylaxis.errors import (
    DeviceResolutionError,
    ProphylaxisExecutionError,
    UnsupportedPlatformError,
)
from atlas.prophylaxis.evaluation import evaluate_uptime
from atlas.prophylaxis.models import (
    CheckId,
    CollectionStatus,
    CollectorId,
    CPUUtilizationValues,
    DeviceFactsValues,
    DiagnosticValues,
    EolStatusValues,
    EoxLifecycleRecord,
    EvaluationOutcome,
    FirmwareRecommendationValues,
    OSVersionValues,
    SystemMetricValues,
    UptimeValues,
    expand_check_selection,
)
from atlas.prophylaxis.charts import render_metric_svg
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry
from atlas.prophylaxis.run_models import (
    ModelInventoryValues,
    ProphylaxisChart,
    ProphylaxisCheckResult,
    ProphylaxisRun,
    RunSummary,
    SerialInventoryValues,
    utc_now,
)
from atlas.prophylaxis.run_store import RunStoreError
from atlas.prophylaxis.service import (
    device_target,
    resolve_execution_profile,
    resolve_service_port,
    selected_check_values,
)
from atlas.services.errors import DependencyError


logger = logging.getLogger("atlas.prophylaxis.runs")
OPERATION = "selected_checks"
SERIAL_INVENTORY_CHECK = CheckId.SERIAL_INVENTORY.value
MODEL_INVENTORY_CHECK = CheckId.MODEL_INVENTORY.value
INVENTORY_CHECKS = (SERIAL_INVENTORY_CHECK, MODEL_INVENTORY_CHECK)
EXTERNAL_LIFECYCLE_CHECKS = frozenset({
    CheckId.FIRMWARE_RECOMMENDATION,
    CheckId.EOL_STATUS,
})
CISCO_LIFECYCLE_OS_TYPES = {
    "cisco-ios": "IOS",
    "cisco-ios-xe": "IOS-XE",
    "cisco-cbs": None,
    "cisco-cbs250-350": None,
    "cisco-nxos": "NX-OS",
    "cisco-asa": "ASA",
}
CISCO_LIFECYCLE_PLATFORMS = frozenset(CISCO_LIFECYCLE_OS_TYPES)
IDENTITY_COLLECTORS = frozenset({
    CollectorId.DEVICE_FACTS,
    CollectorId.IOS_IDENTITY,
})
ZABBIX_CHART_METRICS = {CheckId.CPU_UTILIZATION.value: "cpu"}
ZABBIX_CHART_WINDOW = "1h"
MAX_STORED_EOX_RECORDS = 50


class DeviceFactsRunService:
    """Run selected checks plus inventory identity controls for each device."""

    def __init__(
        self,
        netbox,
        openbao,
        *,
        backend,
        store,
        concurrency: int,
        registry: AnsiblePlatformRegistry | None = None,
        collector_registry: CollectorRegistry | None = None,
        inventory_reconciliation_enabled: bool = False,
        zabbix_monitoring=None,
        oxidized=None,
        cisco_support=None,
        chart_renderer=render_metric_svg,
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.backend = backend
        self.store = store
        self.concurrency = concurrency
        self.registry = registry or AnsiblePlatformRegistry()
        self.collector_registry = collector_registry or CollectorRegistry()
        self.inventory_reconciliation_enabled = inventory_reconciliation_enabled
        self.zabbix_monitoring = zabbix_monitoring
        self.oxidized = oxidized
        self.cisco_support = cisco_support
        self.chart_renderer = chart_renderer
        self._slots = threading.BoundedSemaphore(concurrency)
        self._run_lock = threading.Lock()
        self._oxidized_nodes: dict[str, tuple[dict, ...]] = {}
        self._oxidized_error: str | None = None

    def submit(self) -> ProphylaxisRun:
        return self.store.create()

    def get(self, run_id) -> ProphylaxisRun:
        return self.store.get(run_id)

    def run(self, run_id) -> None:
        # The Oxidized snapshot belongs to one run and is shared by that run's
        # per-device workers. Serializing run coordinators prevents snapshots
        # from two API requests from overwriting each other.
        with self._run_lock:
            self._run_serial(run_id)

    def _run_serial(self, run_id) -> None:
        pending = self.store.get(run_id)
        running = pending.model_copy(
            update={"status": "RUNNING", "started_at": utc_now()}
        )
        self.store.save(running)
        try:
            devices = self.netbox.get_prophylaxis_devices()
            self._prepare_oxidized_backup_state(devices)
            with ThreadPoolExecutor(
                max_workers=self.concurrency,
                thread_name_prefix="atlas-prophylaxis",
            ) as executor:
                per_device = tuple(executor.map(self._collect_guarded, devices))
            results = tuple(
                result for device_results in per_device for result in device_results
            )
            summary = self._summary(results)
            if summary.failure and not summary.success and not summary.warning:
                status = "FAILED"
            elif summary.failure or summary.skipped or summary.warning:
                status = "PARTIAL"
            else:
                status = "COMPLETED"
            completed = running.model_copy(
                update={
                    "status": status,
                    "finished_at": utc_now(),
                    "summary": summary,
                    "results": results,
                    "charts": self._generate_charts_guarded(run_id, results),
                }
            )
            self.store.save(completed)
            logger.info(
                "prophylaxis_run_completed",
                extra={
                    "event": "prophylaxis_run_completed",
                    "run_id": str(run_id),
                    "operation": OPERATION,
                    "status": status,
                    **summary.model_dump(),
                },
            )
        except DependencyError:
            self._fail_run(running, "dependency_unavailable")
        except Exception as exc:
            logger.error(
                "prophylaxis_run_failed",
                extra={
                    "event": "prophylaxis_run_failed",
                    "run_id": str(run_id),
                    "operation": OPERATION,
                    "exception_type": type(exc).__name__,
                },
            )
            self._fail_run(running, "execution_failed")

    def _generate_charts_guarded(
        self,
        run_id,
        results: tuple[ProphylaxisCheckResult, ...],
    ) -> tuple[ProphylaxisChart, ...]:
        try:
            return self._generate_charts(run_id, results)
        except Exception as exc:
            logger.warning(
                "prophylaxis_chart_generation_failed",
                extra={
                    "event": "prophylaxis_chart_generation_failed",
                    "run_id": str(run_id),
                    "reason_code": "chart_generation_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            return ()

    def _generate_charts(
        self,
        run_id,
        results: tuple[ProphylaxisCheckResult, ...],
    ) -> tuple[ProphylaxisChart, ...]:
        if self.zabbix_monitoring is None:
            return ()
        candidates = {
            (result.device_id, result.device_name, result.check)
            for result in results
            if result.device_id is not None and result.check in ZABBIX_CHART_METRICS
        }
        if not candidates:
            return ()
        try:
            hosts = {
                self.zabbix_monitoring._tags(host).get("netbox_id"): host
                for host in self.zabbix_monitoring.hosts()
            }
        except DependencyError:
            return tuple(
                self._unavailable_chart(
                    device_id, device_name, check, "zabbix_unavailable"
                )
                for device_id, device_name, check in sorted(candidates)
            )

        charts = []
        for device_id, device_name, check in sorted(candidates):
            metric = ZABBIX_CHART_METRICS[check]
            host = hosts.get(str(device_id))
            if host is None:
                charts.append(
                    self._unavailable_chart(
                        device_id, device_name, check, "monitoring_host_not_found"
                    )
                )
                continue
            try:
                data = self.zabbix_monitoring.metric(
                    str(host["hostid"]), metric, ZABBIX_CHART_WINDOW
                )
                points = list(data["points"])
                if not points:
                    charts.append(
                        self._unavailable_chart(
                            device_id,
                            device_name,
                            check,
                            "metric_history_unavailable",
                        )
                    )
                    continue
                artifact = f"device-{device_id}-{metric}-{ZABBIX_CHART_WINDOW}.svg"
                svg = self.chart_renderer(
                    device_name=device_name,
                    metric=metric,
                    unit=str(data.get("unit") or ""),
                    window=ZABBIX_CHART_WINDOW,
                    points=points,
                )
                self.store.write_chart(run_id, artifact, svg)
                charts.append(
                    ProphylaxisChart(
                        device_id=device_id,
                        device_name=device_name,
                        check=check,
                        metric=metric,
                        window=ZABBIX_CHART_WINDOW,
                        status="generated",
                        artifact=artifact,
                        point_count=len(points),
                    )
                )
            except (
                DependencyError,
                KeyError,
                TypeError,
                ValueError,
                RunStoreError,
            ):
                charts.append(
                    self._unavailable_chart(
                        device_id, device_name, check, "chart_generation_failed"
                    )
                )
        return tuple(charts)

    @staticmethod
    def _unavailable_chart(
        device_id: int,
        device_name: str,
        check: str,
        error: str,
    ) -> ProphylaxisChart:
        return ProphylaxisChart(
            device_id=device_id,
            device_name=device_name,
            check=check,
            metric=ZABBIX_CHART_METRICS[check],
            window=ZABBIX_CHART_WINDOW,
            status="unavailable",
            error=error,
        )

    def _fail_run(self, running: ProphylaxisRun, code: str) -> None:
        self.store.save(
            running.model_copy(
                update={
                    "status": "FAILED",
                    "finished_at": utc_now(),
                    "error": code,
                }
            )
        )

    @staticmethod
    def _selected_checks(
        device: dict,
    ) -> tuple[tuple[CheckId, ...], tuple[str, ...]]:
        fields = device.get("custom_fields")
        fields = fields if isinstance(fields, dict) else {}
        raw_values = selected_check_values(fields.get("profylaxia_checks"))
        return expand_check_selection(raw_values)

    def _prepare_oxidized_backup_state(self, devices: list[dict]) -> None:
        self._oxidized_nodes = {}
        self._oxidized_error = None
        backup_requested = any(
            CheckId.CONFIGURATION_BACKUP in self._selected_checks(device)[0]
            for device in devices
        )
        if not backup_requested:
            return
        if self.oxidized is None:
            self._oxidized_error = "oxidized_not_configured"
            return
        try:
            nodes = self.oxidized.get_nodes()
        except DependencyError:
            self._oxidized_error = "oxidized_unavailable"
            return
        grouped: dict[str, list[dict]] = {}
        for node in nodes:
            name = node.get("name")
            if isinstance(name, str) and name.strip():
                grouped.setdefault(name.strip(), []).append(node)
        self._oxidized_nodes = {
            name: tuple(entries) for name, entries in grouped.items()
        }

    @staticmethod
    def _oxidized_timestamp(value: object) -> datetime | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return (
            parsed.astimezone(timezone.utc)
            if parsed.tzinfo
            else parsed.replace(tzinfo=timezone.utc)
        )

    def _configuration_backup_values(self, device_name: str) -> DiagnosticValues:
        if self._oxidized_error is not None:
            return DiagnosticValues(
                outcome=EvaluationOutcome.UNKNOWN,
                reason_code=self._oxidized_error,
                summary="Oxidized backup state is unavailable",
            )
        nodes = self._oxidized_nodes.get(device_name, ())
        if not nodes:
            return DiagnosticValues(
                outcome=EvaluationOutcome.WARNING,
                reason_code="oxidized_node_or_backup_missing",
                summary="No completed Oxidized backup was found for the device",
                metrics={"manual_backup_required": True},
            )
        if len(nodes) != 1:
            return DiagnosticValues(
                outcome=EvaluationOutcome.UNKNOWN,
                reason_code="oxidized_node_ambiguous",
                summary="Multiple Oxidized nodes have the same device name",
                metrics={"matching_nodes": len(nodes)},
            )
        node = nodes[0]
        last = node.get("last")
        if not isinstance(last, dict):
            return DiagnosticValues(
                outcome=EvaluationOutcome.WARNING,
                reason_code="oxidized_backup_never_completed",
                summary="Oxidized has no completed backup result for the device",
                metrics={"manual_backup_required": True},
            )
        status = last.get("status")
        status_text = status.strip().lower() if isinstance(status, str) else ""
        completed_at = self._oxidized_timestamp(last.get("end"))
        metrics: dict[str, str | int | float | bool | None] = {
            "oxidized_status": status_text or None,
            "last_completed_at": (
                completed_at.isoformat() if completed_at is not None else None
            ),
        }
        if completed_at is not None:
            metrics["age_hours"] = round(
                (datetime.now(timezone.utc) - completed_at).total_seconds() / 3600,
                2,
            )
        if status_text == "success":
            metrics["manual_backup_required"] = False
            return DiagnosticValues(
                outcome=EvaluationOutcome.PASS,
                summary="The most recent Oxidized backup completed successfully",
                metrics=metrics,
            )
        metrics["manual_backup_required"] = True
        return DiagnosticValues(
            outcome=EvaluationOutcome.FAIL,
            reason_code="oxidized_backup_failed",
            summary="The most recent Oxidized backup did not complete successfully",
            metrics=metrics,
        )

    def _collect_guarded(
        self,
        device: dict,
    ) -> tuple[ProphylaxisCheckResult, ...]:
        with self._slots:
            try:
                return self._collect(device)
            except Exception as exc:
                device_id, name, management_ip, platform = self._identity(device)
                known, unknown = self._selected_checks(device)
                checks = tuple(check.value for check in known) + unknown
                logger.warning(
                    "prophylaxis_device_failed",
                    extra={
                        "event": "prophylaxis_device_failed",
                        "device": name,
                        "platform": platform,
                        "operation": OPERATION,
                        "reason_code": "unexpected_execution_error",
                        "exception_type": type(exc).__name__,
                    },
                )
                return tuple(
                    self._check_error(
                        device_id,
                        name,
                        management_ip,
                        platform,
                        check,
                        "failed",
                        "unexpected_execution_error",
                        log_event=False,
                    )
                    for check in checks
                )

    @staticmethod
    def _identity(device: dict) -> tuple[int | None, str, str | None, str | None]:
        device_id = device.get("id")
        if isinstance(device_id, bool) or not isinstance(device_id, int):
            device_id = None
        name = device.get("name")
        if not isinstance(name, str) or not name.strip():
            name = str(device_id or "unknown")
        primary = device.get("primary_ip4") or device.get("primary_ip6") or {}
        address = primary.get("address") if isinstance(primary, dict) else None
        management_ip = (
            address.split("/", 1)[0] if isinstance(address, str) else None
        )
        platform_data = device.get("platform")
        platform = (
            platform_data.get("slug")
            if isinstance(platform_data, dict)
            else None
        )
        return device_id, name.strip(), management_ip, platform

    def _collect(self, device: dict) -> tuple[ProphylaxisCheckResult, ...]:
        device_id, name, management_ip, platform = self._identity(device)
        selected_checks, unknown_checks = self._selected_checks(device)
        backup_selected = CheckId.CONFIGURATION_BACKUP in selected_checks
        lifecycle_checks = tuple(
            check
            for check in selected_checks
            if check in EXTERNAL_LIFECYCLE_CHECKS
        )
        known_checks = tuple(
            check
            for check in selected_checks
            if check != CheckId.CONFIGURATION_BACKUP
            and check not in EXTERNAL_LIFECYCLE_CHECKS
        )
        results = [
            self._check_error(
                device_id, name, management_ip, platform, check,
                "skipped", "unsupported_check",
            )
            for check in unknown_checks
        ]
        if backup_selected:
            backup_values = self._configuration_backup_values(name)
            results.append(
                self._success_result(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    CheckId.CONFIGURATION_BACKUP,
                    backup_values,
                )
            )
        try:
            target = device_target(device)
            device_id, name, management_ip, platform = (
                target.id, target.name, target.host, target.platform,
            )
            base_profile = self.registry.resolve(target.platform)
            profile, resolved_port = resolve_execution_profile(
                self.netbox,
                target,
                base_profile,
            )
            plan = self.collector_registry.plan(known_checks, profile)
        except Exception as exc:
            status, code = self._classify_exception(exc)
            results.extend(
                self._check_error(
                    device_id, name, management_ip, platform, check.value,
                    status, code,
                )
                for check in known_checks + lifecycle_checks
            )
            if self.inventory_reconciliation_enabled:
                results.extend(
                    self._inventory_errors(
                        device_id, name, management_ip, platform, status, code,
                    )
                )
            return tuple(results)

        lifecycle_supported = (
            bool(lifecycle_checks)
            and target.platform in CISCO_LIFECYCLE_PLATFORMS
            and self.cisco_support is not None
        )
        if lifecycle_checks and not lifecycle_supported:
            results.extend(
                self._check_error(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    check.value,
                    "skipped",
                    "unsupported_check",
                )
                for check in lifecycle_checks
            )

        identity_required = (
            self.inventory_reconciliation_enabled or lifecycle_supported
        )
        if (
            identity_required
            and any(
                profile.supports_collector(collector_id)
                for collector_id in IDENTITY_COLLECTORS
            )
        ):
            if not any(
                task.collector.id in IDENTITY_COLLECTORS
                for task in plan.tasks
            ):
                try:
                    facts_collector = self.collector_registry.resolve_check(
                        CheckId.DEVICE_FACTS,
                        profile,
                    )
                except ValueError:
                    results.extend(
                        self._inventory_errors(
                            device_id,
                            name,
                            management_ip,
                            platform,
                            "skipped",
                            "unsupported_check",
                        )
                    )
                else:
                    plan = CollectionPlan(
                        tasks=plan.tasks
                        + (CollectorTask(collector=facts_collector, checks=()),),
                        unsupported_checks=plan.unsupported_checks,
                    )
        elif identity_required:
            results.extend(
                self._inventory_errors(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    "skipped",
                    "unsupported_check",
                )
            )

        results.extend(
            self._check_error(
                device_id, name, management_ip, platform, check.value,
                "skipped", "unsupported_check",
            )
            for check in plan.unsupported_checks
        )
        if not plan.tasks:
            return tuple(results)

        prophylaxis_connection = None
        snmp_credentials = None
        identity_facts: DeviceFactsValues | None = None
        inventory_results: dict[str, ProphylaxisCheckResult] = {}
        for task in plan.tasks:
            try:
                if task.collector.id == CollectorId.SNMP_SYSTEM:
                    execution_profile = profile
                    port = 161
                    telnet_fallback_port = None
                    if snmp_credentials is None:
                        try:
                            snmp_credentials = self.openbao.get_snmp_credentials(
                                target.vendor,
                                target.name,
                            )[0]
                        except DeviceValidationError as exc:
                            if (
                                exc.code != "snmp_credential_not_found"
                                or target.credential_profile == target.name
                            ):
                                raise
                            snmp_credentials = self.openbao.get_snmp_credentials(
                                target.vendor,
                                target.credential_profile,
                            )[0]
                        if snmp_credentials.get("version") != "2c":
                            raise DeviceValidationError(
                                "SNMP version is not supported by this collector",
                                code="unsupported_snmp_version",
                                category="skipped",
                            )
                    credentials = snmp_credentials
                else:
                    if prophylaxis_connection is None:
                        execution_profile, port = profile, resolved_port
                        telnet_services = (
                            self.netbox.get_device_services(
                                target.id,
                                execution_profile.telnet_fallback_service_name,
                            )
                            if execution_profile.telnet_fallback_service_name
                            else []
                        )
                        telnet_fallback_port = (
                            resolve_service_port(
                                telnet_services,
                                execution_profile.telnet_fallback_default_port,
                            )
                            if telnet_services
                            else None
                        )
                        credentials = self.openbao.get_device_credentials(
                            target.vendor,
                            target.credential_profile,
                            authentication=execution_profile.authentication,
                            purpose="prophylaxis",
                        )
                        prophylaxis_connection = (
                            execution_profile,
                            port,
                            telnet_fallback_port,
                            credentials,
                        )
                    (
                        execution_profile,
                        port,
                        telnet_fallback_port,
                        credentials,
                    ) = prophylaxis_connection
                raw = self.backend.execute(
                    host=target.host,
                    port=port,
                    profile=execution_profile,
                    collector_id=task.collector.id,
                    check_id=(
                        task.checks[0]
                        if task.checks
                        else CheckId.DEVICE_FACTS
                    ),
                    credentials=credentials,
                    telnet_fallback_port=telnet_fallback_port,
                    device_model=target.model,
                )
            except Exception as exc:
                status, code = self._classify_exception(exc)
                results.extend(
                    self._check_error(
                        device_id, name, management_ip, platform, check.value,
                        status, code,
                    )
                    for check in task.checks
                )
                if (
                    identity_required
                    and task.collector.id in IDENTITY_COLLECTORS
                ):
                    results.extend(
                        self._inventory_errors(
                            device_id,
                            name,
                            management_ip,
                            platform,
                            status,
                            code,
                        )
                    )
                continue

            if raw.status != "ok":
                status = "skipped" if raw.status == "unsupported" else "failed"
                results.extend(
                    self._check_error(
                        device_id, name, management_ip, platform, check.value,
                        status, raw.error_code or "execution_failed",
                    )
                    for check in task.checks
                )
                if (
                    identity_required
                    and task.collector.id in IDENTITY_COLLECTORS
                ):
                    results.extend(
                        self._inventory_errors(
                            device_id,
                            name,
                            management_ip,
                            platform,
                            status,
                            raw.error_code or "execution_failed",
                        )
                    )
                continue

            if (
                identity_required
                and task.collector.id in IDENTITY_COLLECTORS
            ):
                try:
                    facts = task.collector.normalize(CheckId.DEVICE_FACTS, raw)
                    if not isinstance(facts, DeviceFactsValues):
                        raise ValueError("collector returned invalid device facts")
                    identity_facts = facts
                    reconciled = self._reconcile_inventory(
                        device_id,
                        name,
                        management_ip,
                        platform,
                        facts,
                    )
                    results.extend(reconciled)
                    inventory_results.update({
                        result.check: result for result in reconciled
                    })
                except Exception as exc:
                    status, code = self._classify_exception(exc)
                    results.extend(
                        self._inventory_errors(
                            device_id,
                            name,
                            management_ip,
                            platform,
                            status,
                            code,
                        )
                    )

            for check in task.checks:
                try:
                    values = task.collector.normalize(check, raw)
                    result = self._success_result(
                        device_id, name, management_ip, platform, check, values,
                    )
                except Exception as exc:
                    status, code = self._classify_exception(exc)
                    result = self._check_error(
                        device_id, name, management_ip, platform, check.value,
                        status, code,
                    )
                results.append(result)
        if lifecycle_supported:
            results.extend(self._cisco_lifecycle_results(
                device_id=device_id,
                name=name,
                management_ip=management_ip,
                platform=target.platform,
                checks=lifecycle_checks,
                facts=identity_facts,
                inventory_results=inventory_results,
            ))
        return tuple(results)

    @staticmethod
    def _cisco_error_code(exc: Exception) -> str:
        reason = getattr(exc, "reason", "unavailable")
        return {
            "secret_not_found": "cisco_secret_not_found",
            "invalid_credential_schema": "cisco_secret_invalid",
            "authentication_failed": "cisco_authentication_failed",
            "not_authorized": "cisco_api_not_authorized",
            "connection_failed": "cisco_connection_failed",
            "tls_validation_failed": "cisco_tls_validation_failed",
            "invalid_response": "cisco_invalid_response",
            "invalid_request": "cisco_identifier_invalid",
            "request_failed": "cisco_request_rejected",
        }.get(reason, "cisco_api_unavailable")

    @staticmethod
    def _external_unknown(
        reason_code: str,
        summary: str,
        **metrics,
    ) -> DiagnosticValues:
        return DiagnosticValues(
            outcome=EvaluationOutcome.UNKNOWN,
            reason_code=reason_code,
            summary=summary,
            metrics=metrics,
        )

    @staticmethod
    def _verified_inventory_value(
        result: ProphylaxisCheckResult | None,
        expected_type,
        field: str,
    ) -> str | None:
        if (
            result is None
            or not isinstance(result.values, expected_type)
            or result.values.state not in {"matched", "populated"}
        ):
            return None
        return getattr(result.values, field)

    def _cisco_lifecycle_results(
        self,
        *,
        device_id: int,
        name: str,
        management_ip: str,
        platform: str,
        checks: tuple[CheckId, ...],
        facts: DeviceFactsValues | None,
        inventory_results: dict[str, ProphylaxisCheckResult],
    ) -> tuple[ProphylaxisCheckResult, ...]:
        serial_number = self._verified_inventory_value(
            inventory_results.get(SERIAL_INVENTORY_CHECK),
            SerialInventoryValues,
            "netbox_serial",
        )
        product_id = self._verified_inventory_value(
            inventory_results.get(MODEL_INVENTORY_CHECK),
            ModelInventoryValues,
            "netbox_model",
        )
        installed_release = facts.os_version if facts is not None else None
        missing = tuple(
            name
            for name, value in (
                ("serial", serial_number),
                ("product_id", product_id),
                ("installed_release", installed_release),
            )
            if value is None
        )
        if missing:
            values = self._external_unknown(
                "cisco_identity_not_verified",
                "Cisco lifecycle lookup requires matching NetBox and device identity",
                missing_identity=",".join(missing),
            )
            return tuple(
                self._success_result(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    check,
                    values,
                )
                for check in checks
            )

        results: list[ProphylaxisCheckResult] = []
        if CheckId.FIRMWARE_RECOMMENDATION in checks:
            try:
                values = self._firmware_recommendation_values(
                    product_id,
                    installed_release,
                )
            except (CiscoSupportAPIError, DependencyError) as exc:
                values = self._external_unknown(
                    self._cisco_error_code(exc),
                    "Cisco Software Suggestion could not be evaluated",
                    product_id=product_id,
                    installed_release=installed_release,
                )
            results.append(self._success_result(
                device_id,
                name,
                management_ip,
                platform,
                CheckId.FIRMWARE_RECOMMENDATION,
                values,
            ))

        if CheckId.EOL_STATUS in checks:
            try:
                values = self._eol_status_values(
                    serial_number=serial_number,
                    product_id=product_id,
                    installed_release=installed_release,
                    platform=platform,
                )
            except (CiscoSupportAPIError, DependencyError) as exc:
                values = self._external_unknown(
                    self._cisco_error_code(exc),
                    "Cisco EoX could not be evaluated",
                    serial_number=serial_number,
                    product_id=product_id,
                    installed_release=installed_release,
                )
            results.append(self._success_result(
                device_id,
                name,
                management_ip,
                platform,
                CheckId.EOL_STATUS,
                values,
            ))
        return tuple(results)

    def _firmware_recommendation_values(
        self,
        product_id: str,
        installed_release: str,
    ) -> FirmwareRecommendationValues:
        suggestions = self.cisco_support.software_suggestions(
            product_id,
            installed_release,
        )
        if any(
            item.product_id.casefold() != product_id.casefold()
            for item in suggestions
        ):
            raise CiscoSupportAPIError("invalid_response")
        suggested = tuple(item for item in suggestions if item.suggested)
        releases = tuple(dict.fromkeys(
            value
            for item in suggested
            for value in (item.alternate_release, item.release)
            if value
        ))
        comparable = {value.casefold() for value in releases}
        software_type = next(
            (item.software_type for item in suggestions if item.software_type),
            None,
        )
        if installed_release.casefold() in comparable:
            outcome = EvaluationOutcome.PASS
            reason_code = "installed_release_suggested"
        elif releases:
            outcome = EvaluationOutcome.WARNING
            reason_code = "software_update_available"
        elif suggestions:
            outcome = EvaluationOutcome.UNKNOWN
            reason_code = "cisco_no_suggested_release"
        else:
            outcome = EvaluationOutcome.UNKNOWN
            reason_code = "cisco_pid_not_covered"
        try:
            return FirmwareRecommendationValues(
                outcome=outcome,
                reason_code=reason_code,
                product_id=product_id,
                installed_release=installed_release,
                suggested_releases=releases,
                software_type=software_type,
            )
        except ValueError:
            raise CiscoSupportAPIError("invalid_response") from None

    @staticmethod
    def _iso_date(value: str | None) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise CiscoSupportAPIError("invalid_response") from None

    @classmethod
    def _lifecycle_record(
        cls,
        scope: str,
        record: CiscoEoxRecord,
    ) -> EoxLifecycleRecord:
        try:
            return EoxLifecycleRecord(
                scope=scope,
                product_id=record.product_id,
                description=record.description,
                bulletin_number=record.bulletin_number,
                bulletin_url=record.bulletin_url,
                announcement_date=cls._iso_date(record.announcement_date),
                end_of_sale_date=cls._iso_date(record.end_of_sale_date),
                end_of_software_maintenance_date=cls._iso_date(
                    record.end_of_software_maintenance_date
                ),
                end_of_security_support_date=cls._iso_date(
                    record.end_of_security_support_date
                ),
                last_date_of_support=cls._iso_date(
                    record.last_date_of_support
                ),
                updated_date=cls._iso_date(record.updated_date),
            )
        except ValueError:
            raise CiscoSupportAPIError("invalid_response") from None

    def _eol_status_values(
        self,
        *,
        serial_number: str,
        product_id: str,
        installed_release: str,
        platform: str,
    ) -> EolStatusValues | DiagnosticValues:
        try:
            os_type = CISCO_LIFECYCLE_OS_TYPES[platform]
        except KeyError:
            raise CiscoSupportAPIError("invalid_request") from None
        records: list[EoxLifecycleRecord] = []
        record_counts = {"hardware": 0, "software": 0}
        states = {"hardware": "unavailable", "software": "unavailable"}
        errors: list[str] = []
        for scope, lookup in (
            ("hardware", lambda: self.cisco_support.hardware_eox(serial_number)),
            (
                "software",
                lambda: self.cisco_support.software_eox(
                    installed_release,
                    os_type,
                ),
            ),
        ):
            try:
                response = lookup()
                normalized = tuple(
                    self._lifecycle_record(scope, record)
                    for record in response.records
                )
            except (CiscoSupportAPIError, DependencyError) as exc:
                errors.append(self._cisco_error_code(exc))
                continue
            states[scope] = "record_found" if normalized else "no_record"
            record_counts[scope] = len(normalized)
            records.extend(normalized)

        if states["hardware"] == states["software"] == "unavailable":
            return self._external_unknown(
                errors[0] if errors else "cisco_api_unavailable",
                "Cisco EoX could not be evaluated",
                serial_number=serial_number,
                product_id=product_id,
                installed_release=installed_release,
            )

        today = date.today()
        support_ended = any(
            record.last_date_of_support is not None
            and record.last_date_of_support <= today
            for record in records
        )
        if support_ended:
            outcome = EvaluationOutcome.FAIL
            reason_code = "last_date_of_support_passed"
        elif records:
            outcome = EvaluationOutcome.WARNING
            reason_code = "eox_record_published"
        else:
            outcome = EvaluationOutcome.UNKNOWN
            reason_code = (
                "cisco_eox_partial_data"
                if "unavailable" in states.values()
                else "cisco_eox_record_not_found"
            )
        try:
            return EolStatusValues(
                outcome=outcome,
                reason_code=reason_code,
                serial_number=serial_number,
                product_id=product_id,
                installed_release=installed_release,
                os_type=os_type,
                hardware_state=states["hardware"],
                software_state=states["software"],
                hardware_record_count=record_counts["hardware"],
                software_record_count=record_counts["software"],
                records_truncated=len(records) > MAX_STORED_EOX_RECORDS,
                records=tuple(records[:MAX_STORED_EOX_RECORDS]),
            )
        except ValueError:
            raise CiscoSupportAPIError("invalid_response") from None

    @staticmethod
    def _normalized_inventory_value(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized or None

    def _inventory_errors(
        self,
        device_id: int | None,
        name: str,
        management_ip: str | None,
        platform: str | None,
        status: str,
        code: str,
    ) -> tuple[ProphylaxisCheckResult, ...]:
        return tuple(
            self._check_error(
                device_id,
                name,
                management_ip,
                platform,
                check,
                status,
                code,
            )
            for check in INVENTORY_CHECKS
        )

    def _reconcile_inventory(
        self,
        device_id: int | None,
        name: str,
        management_ip: str | None,
        platform: str | None,
        facts: DeviceFactsValues,
    ) -> tuple[ProphylaxisCheckResult, ...]:
        if device_id is None:
            return self._inventory_errors(
                device_id,
                name,
                management_ip,
                platform,
                "failed",
                "device_not_found",
            )
        try:
            current = self.netbox.get(f"/api/dcim/devices/{device_id}/")
        except DependencyError:
            return (
                self._check_error(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    SERIAL_INVENTORY_CHECK,
                    "failed",
                    "serial_netbox_read_failed",
                ),
                self._check_error(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    MODEL_INVENTORY_CHECK,
                    "failed",
                    "model_netbox_read_failed",
                ),
            )
        return (
            self._reconcile_serial(
                device_id, name, management_ip, platform, facts, current,
            ),
            self._reconcile_model(
                device_id, name, management_ip, platform, facts, current,
            ),
        )

    def _reconcile_serial(
        self,
        device_id: int | None,
        name: str,
        management_ip: str | None,
        platform: str | None,
        facts: DeviceFactsValues,
        current: dict,
    ) -> ProphylaxisCheckResult:
        collected = self._normalized_inventory_value(facts.serial_number)
        if collected is None:
            return self._check_error(
                device_id,
                name,
                management_ip,
                platform,
                SERIAL_INVENTORY_CHECK,
                "incomplete",
                "serial_not_collected",
            )

        stored = self._normalized_inventory_value(current.get("serial"))
        state = "matched"

        if stored is None:
            try:
                updated = self.netbox.patch(
                    f"/api/dcim/devices/{device_id}/",
                    {"serial": collected},
                )
            except DependencyError:
                return self._check_error(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    SERIAL_INVENTORY_CHECK,
                    "failed",
                    "serial_netbox_update_failed",
                )
            stored = self._normalized_inventory_value(updated.get("serial"))
            if stored is None:
                return self._check_error(
                    device_id,
                    name,
                    management_ip,
                    platform,
                    SERIAL_INVENTORY_CHECK,
                    "failed",
                    "serial_netbox_update_failed",
                )
            state = "populated"

        matches = stored.casefold() == collected.casefold()
        if not matches:
            state = "mismatch"

        outcome = (
            EvaluationOutcome.FAIL
            if state == "mismatch"
            else EvaluationOutcome.PASS
        )
        status = "failed" if state == "mismatch" else "success"
        log = logger.warning if state == "mismatch" else logger.info
        log(
            "prophylaxis_serial_reconciled",
            extra={
                "event": "prophylaxis_serial_reconciled",
                "device": name,
                "platform": platform,
                "operation": OPERATION,
                "check": SERIAL_INVENTORY_CHECK,
                "status": status,
                "state": state,
            },
        )
        return ProphylaxisCheckResult(
            check=SERIAL_INVENTORY_CHECK,
            device_id=device_id,
            device_name=name,
            management_ip=management_ip,
            platform=platform,
            status=status,
            evaluation=outcome,
            values=SerialInventoryValues(
                outcome=outcome,
                state=state,
                collected_serial=collected,
                netbox_serial=stored,
            ),
        )

    def _reconcile_model(
        self,
        device_id: int,
        name: str,
        management_ip: str | None,
        platform: str | None,
        facts: DeviceFactsValues,
        current: dict,
    ) -> ProphylaxisCheckResult:
        collected = self._normalized_inventory_value(facts.model)
        if collected is None:
            return self._check_error(
                device_id,
                name,
                management_ip,
                platform,
                MODEL_INVENTORY_CHECK,
                "incomplete",
                "model_not_collected",
            )

        device_type = current.get("device_type")
        stored = self._normalized_inventory_value(
            device_type.get("model") if isinstance(device_type, dict) else None
        )
        if stored is None:
            return self._check_error(
                device_id,
                name,
                management_ip,
                platform,
                MODEL_INVENTORY_CHECK,
                "incomplete",
                "model_not_available_in_netbox",
            )

        state = (
            "matched"
            if stored.casefold() == collected.casefold()
            else "mismatch"
        )
        outcome = (
            EvaluationOutcome.PASS
            if state == "matched"
            else EvaluationOutcome.FAIL
        )
        status = "success" if state == "matched" else "failed"
        log = logger.info if state == "matched" else logger.warning
        log(
            "prophylaxis_model_reconciled",
            extra={
                "event": "prophylaxis_model_reconciled",
                "device": name,
                "platform": platform,
                "operation": OPERATION,
                "check": MODEL_INVENTORY_CHECK,
                "status": status,
                "state": state,
            },
        )
        return ProphylaxisCheckResult(
            check=MODEL_INVENTORY_CHECK,
            device_id=device_id,
            device_name=name,
            management_ip=management_ip,
            platform=platform,
            status=status,
            evaluation=outcome,
            values=ModelInventoryValues(
                outcome=outcome,
                state=state,
                collected_model=collected,
                netbox_model=stored,
            ),
        )

    @staticmethod
    def _success_result(
        device_id: int | None,
        name: str,
        management_ip: str | None,
        platform: str | None,
        check: CheckId,
        values: (
            CPUUtilizationValues
            | DeviceFactsValues
            | OSVersionValues
            | UptimeValues
            | SystemMetricValues
            | DiagnosticValues
            | FirmwareRecommendationValues
            | EolStatusValues
        ),
    ) -> ProphylaxisCheckResult:
        if check == CheckId.DEVICE_FACTS and isinstance(values, DeviceFactsValues):
            return ProphylaxisCheckResult(
                check=check.value,
                device_id=device_id,
                device_name=name,
                management_ip=management_ip,
                platform=platform,
                status="success",
                collection_status=CollectionStatus.COLLECTED,
                evaluation=EvaluationOutcome.PASS,
                facts=values,
            )
        if isinstance(values, (
            SystemMetricValues,
            DiagnosticValues,
            FirmwareRecommendationValues,
            EolStatusValues,
        )):
            status = {
                EvaluationOutcome.PASS: "success",
                EvaluationOutcome.WARNING: "success",
                EvaluationOutcome.FAIL: "failed",
                EvaluationOutcome.UNKNOWN: "incomplete",
                EvaluationOutcome.NOT_APPLICABLE: "skipped",
                EvaluationOutcome.UNSUPPORTED: "skipped",
            }[values.outcome]
            return ProphylaxisCheckResult(
                check=check.value,
                device_id=device_id,
                device_name=name,
                management_ip=management_ip,
                platform=platform,
                status=status,
                collection_status=CollectionStatus.COLLECTED,
                evaluation=values.outcome,
                values=values,
            )
        if check == CheckId.UPTIME and isinstance(values, UptimeValues):
            outcome, reason_code = evaluate_uptime(values)
            return ProphylaxisCheckResult(
                check=check.value,
                device_id=device_id,
                device_name=name,
                management_ip=management_ip,
                platform=platform,
                status="success",
                collection_status=CollectionStatus.COLLECTED,
                evaluation=outcome,
                values=values.model_copy(
                    update={"reason_code": reason_code}
                ),
            )
        valid_values = (
            check == CheckId.CPU_UTILIZATION
            and isinstance(values, CPUUtilizationValues)
        ) or (
            check == CheckId.OS_VERSION and isinstance(values, OSVersionValues)
        )
        if not valid_values:
            raise ValueError("collector returned an invalid normalized value")
        return ProphylaxisCheckResult(
            check=check.value,
            device_id=device_id,
            device_name=name,
            management_ip=management_ip,
            platform=platform,
            status="success",
            collection_status=CollectionStatus.COLLECTED,
            evaluation=EvaluationOutcome.PASS,
            values=values,
        )

    @staticmethod
    def _classify_exception(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, UnsupportedPlatformError):
            return "skipped", exc.code
        if isinstance(exc, DeviceValidationError):
            status = "skipped" if exc.category == "skipped" else "failed"
            return status, exc.code
        if isinstance(exc, (DeviceResolutionError, ProphylaxisExecutionError)):
            status = "incomplete" if exc.code == "parse_failed" else "failed"
            return status, exc.code
        if isinstance(exc, DependencyError):
            return "failed", "dependency_unavailable"
        if isinstance(exc, ValueError):
            return "incomplete", "incomplete_collector_result"
        raise exc

    @staticmethod
    def _check_error(
        device_id: int | None,
        name: str,
        management_ip: str | None,
        platform: str | None,
        check: str,
        status: str,
        code: str,
        *,
        log_event: bool = True,
    ) -> ProphylaxisCheckResult:
        if log_event:
            event = (
                "prophylaxis_device_skipped"
                if status == "skipped"
                else "prophylaxis_device_failed"
            )
            log = logger.info if status == "skipped" else logger.warning
            log(
                event,
                extra={
                    "event": event,
                    "device": name,
                    "platform": platform,
                    "operation": OPERATION,
                    "check": check,
                    "status": status,
                    "reason_code": code,
                },
            )
        return ProphylaxisCheckResult(
            check=check,
            device_id=device_id,
            device_name=name,
            management_ip=management_ip,
            platform=platform,
            status=status,
            collection_status=(
                CollectionStatus.UNSUPPORTED
                if status == "skipped"
                else CollectionStatus.COLLECTED
                if status == "incomplete"
                else CollectionStatus.FAILED
            ),
            evaluation=(
                EvaluationOutcome.UNSUPPORTED
                if status == "skipped"
                else EvaluationOutcome.UNKNOWN
                if status == "incomplete"
                else EvaluationOutcome.FAIL
            ),
            error=code,
        )

    @staticmethod
    def _summary(results: tuple[ProphylaxisCheckResult, ...]) -> RunSummary:
        success = sum(
            result.status == "success"
            and result.evaluation != EvaluationOutcome.WARNING
            for result in results
        )
        warning = sum(
            result.evaluation == EvaluationOutcome.WARNING for result in results
        )
        skipped = sum(result.status == "skipped" for result in results)
        failure = sum(
            result.status in {"failed", "incomplete"} for result in results
        )
        return RunSummary(
            total=len(results),
            success=success,
            warning=warning,
            failure=failure,
            skipped=skipped,
        )


__all__ = ["DeviceFactsRunService"]
