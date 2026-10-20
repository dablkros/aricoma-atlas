"""Asynchronous orchestration for NetBox-selected Profylaxia checks."""

from concurrent.futures import ThreadPoolExecutor
import logging
import threading

from atlas.device_credentials import DeviceValidationError
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
from atlas.prophylaxis.models import (
    CHECK_VALUE_ALIASES,
    CheckId,
    CollectorId,
    CPUUtilizationValues,
    DeviceFactsValues,
    EvaluationOutcome,
    OSVersionValues,
    SystemMetricValues,
    UptimeValues,
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
    resolve_service_port,
    selected_check_values,
)
from atlas.services.errors import DependencyError


logger = logging.getLogger("atlas.prophylaxis.runs")
OPERATION = "selected_checks"
SERIAL_INVENTORY_CHECK = "serial_inventory"
MODEL_INVENTORY_CHECK = "model_inventory"
INVENTORY_CHECKS = (SERIAL_INVENTORY_CHECK, MODEL_INVENTORY_CHECK)
ZABBIX_CHART_METRICS = {CheckId.CPU_UTILIZATION.value: "cpu"}
ZABBIX_CHART_WINDOW = "1h"


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
        self.chart_renderer = chart_renderer
        self._slots = threading.BoundedSemaphore(concurrency)

    def submit(self) -> ProphylaxisRun:
        return self.store.create()

    def get(self, run_id) -> ProphylaxisRun:
        return self.store.get(run_id)

    def run(self, run_id) -> None:
        pending = self.store.get(run_id)
        running = pending.model_copy(
            update={"status": "RUNNING", "started_at": utc_now()}
        )
        self.store.save(running)
        try:
            devices = self.netbox.get_prophylaxis_devices()
            with ThreadPoolExecutor(
                max_workers=self.concurrency,
                thread_name_prefix="atlas-prophylaxis",
            ) as executor:
                per_device = tuple(executor.map(self._collect_guarded, devices))
            results = tuple(
                result for device_results in per_device for result in device_results
            )
            summary = self._summary(results)
            if summary.failure and not summary.success:
                status = "FAILED"
            elif summary.failure or summary.skipped:
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
        known: list[CheckId] = []
        unknown: list[str] = []
        for value in sorted(raw_values):
            normalized = CHECK_VALUE_ALIASES.get(value, value)
            try:
                check_id = CheckId(normalized)
            except ValueError:
                unknown.append(value[:128])
                continue
            if check_id not in known:
                known.append(check_id)
        return tuple(known), tuple(unknown)

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
        known_checks, unknown_checks = self._selected_checks(device)
        results = [
            self._check_error(
                device_id, name, management_ip, platform, check,
                "skipped", "unsupported_check",
            )
            for check in unknown_checks
        ]
        try:
            target = device_target(device)
            device_id, name, management_ip, platform = (
                target.id, target.name, target.host, target.platform,
            )
            profile = self.registry.resolve(target.platform)
            plan = self.collector_registry.plan(known_checks, profile)
        except Exception as exc:
            status, code = self._classify_exception(exc)
            results.extend(
                self._check_error(
                    device_id, name, management_ip, platform, check.value,
                    status, code,
                )
                for check in known_checks
            )
            if self.inventory_reconciliation_enabled:
                results.extend(
                    self._inventory_errors(
                        device_id, name, management_ip, platform, status, code,
                    )
                )
            return tuple(results)

        if (
            self.inventory_reconciliation_enabled
            and profile.supports_collector(CollectorId.DEVICE_FACTS)
        ):
            if not any(
                task.collector.id == CollectorId.DEVICE_FACTS
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
        elif self.inventory_reconciliation_enabled:
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
        for task in plan.tasks:
            try:
                if task.collector.id == CollectorId.SNMP_SYSTEM:
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
                        services = (
                            self.netbox.get_device_services(
                                target.id,
                                profile.netbox_service_name,
                            )
                            if profile.netbox_service_name
                            else []
                        )
                        port = resolve_service_port(services, profile.default_port)
                        telnet_services = (
                            self.netbox.get_device_services(
                                target.id,
                                profile.telnet_fallback_service_name,
                            )
                            if profile.telnet_fallback_service_name
                            else []
                        )
                        telnet_fallback_port = (
                            resolve_service_port(
                                telnet_services,
                                profile.telnet_fallback_default_port,
                            )
                            if telnet_services
                            else None
                        )
                        credentials = self.openbao.get_device_credentials(
                            target.vendor,
                            target.credential_profile,
                            authentication=profile.authentication,
                            purpose="prophylaxis",
                        )
                        prophylaxis_connection = (
                            port,
                            telnet_fallback_port,
                            credentials,
                        )
                    (
                        port,
                        telnet_fallback_port,
                        credentials,
                    ) = prophylaxis_connection
                raw = self.backend.execute(
                    host=target.host,
                    port=port,
                    profile=profile,
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
                    self.inventory_reconciliation_enabled
                    and task.collector.id == CollectorId.DEVICE_FACTS
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
                    self.inventory_reconciliation_enabled
                    and task.collector.id == CollectorId.DEVICE_FACTS
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
                self.inventory_reconciliation_enabled
                and task.collector.id == CollectorId.DEVICE_FACTS
            ):
                try:
                    facts = task.collector.normalize(CheckId.DEVICE_FACTS, raw)
                    if not isinstance(facts, DeviceFactsValues):
                        raise ValueError("collector returned invalid device facts")
                    results.extend(
                        self._reconcile_inventory(
                            device_id,
                            name,
                            management_ip,
                            platform,
                            facts,
                        )
                    )
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
        return tuple(results)

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
                evaluation=EvaluationOutcome.PASS,
                facts=values,
            )
        if isinstance(values, SystemMetricValues):
            status = {
                EvaluationOutcome.PASS: "success",
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
                evaluation=values.outcome,
                values=values,
            )
        valid_values = (
            check == CheckId.CPU_UTILIZATION
            and isinstance(values, CPUUtilizationValues)
        ) or (
            check == CheckId.OS_VERSION and isinstance(values, OSVersionValues)
        ) or (
            check == CheckId.UPTIME and isinstance(values, UptimeValues)
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
        success = sum(result.status == "success" for result in results)
        skipped = sum(result.status == "skipped" for result in results)
        failure = sum(
            result.status in {"failed", "incomplete"} for result in results
        )
        return RunSummary(
            total=len(results),
            success=success,
            failure=failure,
            skipped=skipped,
        )


__all__ = ["DeviceFactsRunService"]
