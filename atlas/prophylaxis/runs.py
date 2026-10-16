"""Asynchronous orchestration for NetBox-selected Profylaxia checks."""

from concurrent.futures import ThreadPoolExecutor
import logging
import threading

from atlas.device_credentials import DeviceValidationError
from atlas.prophylaxis.collectors import CollectorRegistry
from atlas.prophylaxis.errors import (
    DeviceResolutionError,
    ProphylaxisExecutionError,
    UnsupportedPlatformError,
)
from atlas.prophylaxis.models import (
    CPU_NETBOX_VALUES,
    CheckId,
    CPUUtilizationValues,
    DeviceFactsValues,
    OSVersionValues,
    UptimeValues,
)
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry
from atlas.prophylaxis.run_models import (
    ProphylaxisCheckResult,
    ProphylaxisRun,
    RunSummary,
    utc_now,
)
from atlas.prophylaxis.service import (
    device_target,
    resolve_service_port,
    selected_check_values,
)
from atlas.services.errors import DependencyError


logger = logging.getLogger("atlas.prophylaxis.runs")
OPERATION = "selected_checks"


class DeviceFactsRunService:
    """Run only the checks selected for each eligible NetBox device."""

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
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.backend = backend
        self.store = store
        self.concurrency = concurrency
        self.registry = registry or AnsiblePlatformRegistry()
        self.collector_registry = collector_registry or CollectorRegistry()
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
            normalized = (
                CheckId.CPU_UTILIZATION.value
                if value in CPU_NETBOX_VALUES
                else value
            )
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
        if not known_checks:
            return tuple(results)

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
            return tuple(results)

        results.extend(
            self._check_error(
                device_id, name, management_ip, platform, check.value,
                "skipped", "unsupported_check",
            )
            for check in plan.unsupported_checks
        )
        if not plan.tasks:
            return tuple(results)

        try:
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
            )
        except Exception as exc:
            status, code = self._classify_exception(exc)
            results.extend(
                self._check_error(
                    device_id, name, management_ip, platform, check.value,
                    status, code,
                )
                for task in plan.tasks for check in task.checks
            )
            return tuple(results)

        for task in plan.tasks:
            try:
                raw = self.backend.execute(
                    host=target.host,
                    port=port,
                    profile=profile,
                    collector_id=task.collector.id,
                    check_id=task.checks[0],
                    credentials=credentials,
                    telnet_fallback_port=telnet_fallback_port,
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
                continue

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
                facts=values,
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
