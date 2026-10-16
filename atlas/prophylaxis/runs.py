"""Asynchronous, read-only device facts collection orchestration."""

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
from atlas.prophylaxis.models import CheckId, DeviceFactsValues
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry
from atlas.prophylaxis.run_models import (
    DeviceFactsResult,
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


class DeviceFactsRunService:
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
            devices = [
                device
                for device in self.netbox.get_prophylaxis_devices()
                if CheckId.DEVICE_FACTS.value
                in selected_check_values(
                    (device.get("custom_fields") or {}).get("profylaxia_checks")
                    if isinstance(device.get("custom_fields"), dict)
                    else None
                )
            ]
            with ThreadPoolExecutor(
                max_workers=self.concurrency,
                thread_name_prefix="atlas-device-facts",
            ) as executor:
                results = tuple(executor.map(self._collect_guarded, devices))
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
                    "operation": "device_facts",
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
                    "operation": "device_facts",
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

    def _collect_guarded(self, device: dict) -> DeviceFactsResult:
        with self._slots:
            try:
                return self._collect(device)
            except Exception as exc:
                device_id, name, management_ip, platform = self._identity(device)
                logger.warning(
                    "prophylaxis_device_failed",
                    extra={
                        "event": "prophylaxis_device_failed",
                        "device": name,
                        "platform": platform,
                        "operation": "device_facts",
                        "reason_code": "unexpected_execution_error",
                        "exception_type": type(exc).__name__,
                    },
                )
                return DeviceFactsResult(
                    device_id=device_id,
                    device_name=name,
                    management_ip=management_ip,
                    platform=platform,
                    status="failed",
                    error="unexpected_execution_error",
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
        management_ip = address.split("/", 1)[0] if isinstance(address, str) else None
        platform_data = device.get("platform")
        platform = platform_data.get("slug") if isinstance(platform_data, dict) else None
        return device_id, name.strip(), management_ip, platform

    def _collect(self, device: dict) -> DeviceFactsResult:
        device_id, name, management_ip, platform = self._identity(device)
        try:
            target = device_target(device)
            device_id, name, management_ip, platform = (
                target.id,
                target.name,
                target.host,
                target.platform,
            )
            profile = self.registry.resolve(target.platform)
            collector = self.collector_registry.resolve_check(CheckId.DEVICE_FACTS)
            if not profile.supports_collector(collector.id):
                raise UnsupportedPlatformError()
            services = (
                self.netbox.get_device_services(
                    target.id,
                    profile.netbox_service_name,
                )
                if profile.netbox_service_name
                else []
            )
            port = resolve_service_port(services, profile.default_port)
            credentials = self.openbao.get_device_credentials(
                target.vendor,
                target.credential_profile,
                authentication=profile.authentication,
            )
            raw = self.backend.execute(
                host=target.host,
                port=port,
                profile=profile,
                collector_id=collector.id,
                check_id=CheckId.DEVICE_FACTS,
                credentials=credentials,
            )
            if raw.status != "ok":
                status = "skipped" if raw.status == "unsupported" else "failed"
                return DeviceFactsResult(
                    device_id=device_id,
                    device_name=name,
                    management_ip=management_ip,
                    platform=platform,
                    status=status,
                    error=raw.error_code or "execution_failed",
                )
            facts = collector.normalize(CheckId.DEVICE_FACTS, raw)
            if not isinstance(facts, DeviceFactsValues):
                raise ValueError("invalid device facts normalizer")
            return DeviceFactsResult(
                device_id=device_id,
                device_name=name,
                management_ip=management_ip,
                platform=platform,
                status="success",
                facts=facts,
            )
        except UnsupportedPlatformError as exc:
            status, code = "skipped", exc.code
        except DeviceValidationError as exc:
            status, code = (
                "skipped" if exc.category == "skipped" else "failed",
                exc.code,
            )
        except (DeviceResolutionError, ProphylaxisExecutionError) as exc:
            status = "incomplete" if exc.code == "parse_failed" else "failed"
            code = exc.code
        except DependencyError:
            status, code = "failed", "dependency_unavailable"
        except ValueError:
            status, code = "incomplete", "incomplete_device_facts"
        return DeviceFactsResult(
            device_id=device_id,
            device_name=name,
            management_ip=management_ip,
            platform=platform,
            status=status,
            error=code,
        )

    @staticmethod
    def _summary(results: tuple[DeviceFactsResult, ...]) -> RunSummary:
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
