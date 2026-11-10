"""Atomic, restricted local storage for Profylaxia run artifacts."""

import json
import os
from pathlib import Path
import re
import shutil
import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

from atlas.prophylaxis.formatting import format_uptime_seconds
from atlas.prophylaxis.models import (
    CheckId,
    CollectionStatus,
    EolStatusValues,
    EvaluationOutcome,
    FirmwareRecommendationValues,
)
from atlas.prophylaxis.run_models import ProphylaxisRawReport, ProphylaxisRun
from atlas.prophylaxis.cisco_raw import (
    CISCO_RAW_SCHEMA_VERSION,
    safe_slug,
    site_artifact_name,
)


class RunStoreError(RuntimeError):
    code = "prophylaxis_run_store_unavailable"


class RunNotFoundError(RuntimeError):
    code = "prophylaxis_run_not_found"


class FileRunStore:
    ARTIFACT_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,126}\.svg")

    def __init__(self, root: Path, *, max_runs: int = 100) -> None:
        self.root = Path(root)
        self.max_runs = max_runs
        self._lock = threading.Lock()

    def _prepare_root(self) -> None:
        if self.root.is_symlink():
            raise RunStoreError()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise RunStoreError()
        os.chmod(self.root, 0o700)

    def _directory(self, run_id: UUID) -> Path:
        return self.root / str(run_id)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        descriptor = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            os.chmod(path, 0o600)
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise RunStoreError() from None

    def create(self) -> ProphylaxisRun:
        run_id = uuid4()
        report_path = self._directory(run_id) / "report.txt"
        run = ProphylaxisRun(
            run_id=run_id,
            status="PENDING",
            report_path=str(report_path),
        )
        self.save(run)
        self._prune_terminal_runs(exclude=run_id)
        return run

    def _prune_terminal_runs(self, *, exclude: UUID) -> None:
        """Retain the newest terminal run directories without following links."""
        with self._lock:
            candidates = []
            try:
                entries = tuple(self.root.iterdir())
            except OSError:
                raise RunStoreError() from None
            for directory in entries:
                if directory.is_symlink() or not directory.is_dir():
                    continue
                try:
                    run_id = UUID(directory.name)
                except ValueError:
                    continue
                if run_id == exclude:
                    continue
                result = directory / "result.json"
                try:
                    payload = json.loads(result.read_text(encoding="utf-8"))
                    if payload.get("status") not in {"COMPLETED", "PARTIAL", "FAILED"}:
                        continue
                    candidates.append((result.stat().st_mtime, directory))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            candidates.sort(reverse=True)
            for _mtime, directory in candidates[max(0, self.max_runs - 1):]:
                try:
                    shutil.rmtree(directory)
                except OSError:
                    raise RunStoreError() from None

    def save(self, run: ProphylaxisRun) -> None:
        with self._lock:
            self._prepare_root()
            directory = self._directory(run.run_id)
            if directory.is_symlink():
                raise RunStoreError()
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
            self._atomic_write(
                directory / "result.json",
                run.model_dump_json(indent=2) + "\n",
            )
            self._atomic_write(
                directory / "report-data.json",
                json.dumps(
                    self._report_data(run),
                    indent=2,
                    ensure_ascii=False,
                ) + "\n",
            )
            self._atomic_write(directory / "report.txt", self._report(run))

    def write_chart(self, run_id: UUID, name: str, content: str) -> None:
        if not self.ARTIFACT_NAME.fullmatch(name):
            raise RunStoreError()
        with self._lock:
            self._prepare_root()
            directory = self._directory(run_id)
            if directory.is_symlink() or not directory.is_dir():
                raise RunStoreError()
            charts = directory / "charts"
            if charts.is_symlink():
                raise RunStoreError()
            charts.mkdir(mode=0o700, exist_ok=True)
            os.chmod(charts, 0o700)
            self._atomic_write(charts / name, content)

    def write_site_reports(
        self,
        run_id: UUID,
        reports: tuple[dict, ...],
        *,
        started_at: datetime,
    ) -> tuple[ProphylaxisRawReport, ...]:
        """Atomically persist one protected JSON artifact per Tenant/Site."""
        grouped: dict[tuple[str, str, str, str], list[dict]] = {}
        for report in reports:
            device = report.get("device") if isinstance(report, dict) else None
            if not isinstance(device, dict):
                raise RunStoreError()
            tenant = device.get("tenant") or {}
            site = device.get("site") or {}
            tenant_name = str(tenant.get("name") or "No tenant")
            site_name = str(site.get("name") or "No site")
            tenant_slug = safe_slug(tenant.get("slug") or tenant_name, "no-tenant")
            site_slug = safe_slug(site.get("slug") or site_name, "no-site")
            grouped.setdefault(
                (tenant_slug, site_slug, tenant_name, site_name), []
            ).append(report)

        artifacts = []
        with self._lock:
            self._prepare_root()
            directory = self._directory(run_id)
            if directory.is_symlink() or not directory.is_dir():
                raise RunStoreError()
            sites = directory / "sites"
            if sites.is_symlink():
                raise RunStoreError()
            sites.mkdir(mode=0o700, exist_ok=True)
            os.chmod(sites, 0o700)
            for (tenant_slug, site_slug, tenant_name, site_name), devices in sorted(grouped.items()):
                name = site_artifact_name(tenant_slug, site_slug, started_at)
                payload = {
                    "schema_version": CISCO_RAW_SCHEMA_VERSION,
                    "document_type": "atlas_cisco_site_collection",
                    "run_id": str(run_id),
                    "tenant": {"name": tenant_name, "slug": tenant_slug},
                    "site": {"name": site_name, "slug": site_slug},
                    "started_at": started_at.astimezone(timezone.utc).isoformat(),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "devices": sorted(
                        devices,
                        key=lambda item: (
                            item["device"].get("netbox_id") is None,
                            item["device"].get("netbox_id") or 0,
                            item["device"].get("hostname") or "",
                        ),
                    ),
                }
                self._atomic_write(
                    sites / name,
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                )
                artifacts.append(ProphylaxisRawReport(
                    tenant=tenant_slug,
                    site=site_slug,
                    artifact=name,
                    device_count=len(devices),
                ))
        return tuple(artifacts)

    def chart_path(self, run_id: UUID, name: str) -> Path:
        if not self.ARTIFACT_NAME.fullmatch(name):
            raise RunNotFoundError()
        with self._lock:
            path = self._directory(run_id) / "charts" / name
            if path.is_symlink() or not path.is_file():
                raise RunNotFoundError()
            return path

    def get(self, run_id: UUID) -> ProphylaxisRun:
        with self._lock:
            path = self._directory(run_id) / "result.json"
            try:
                if path.is_symlink() or not path.is_file():
                    raise RunNotFoundError()
                payload = json.loads(path.read_text(encoding="utf-8"))
                # ``devices`` is derived from canonical flat results.
                payload.pop("devices", None)
                return ProphylaxisRun.model_validate(payload)
            except RunNotFoundError:
                raise
            except (OSError, ValueError, json.JSONDecodeError):
                raise RunStoreError() from None

    @staticmethod
    def _report_data(run: ProphylaxisRun) -> dict:
        """Build the compact per-device contract for report generation."""

        def compact(value):
            if isinstance(value, dict):
                return {
                    key: compact(item)
                    for key, item in value.items()
                    if item is not None and compact(item) not in ({}, [])
                }
            if isinstance(value, (list, tuple)):
                return [
                    compact(item)
                    for item in value
                    if item is not None and compact(item) not in ({}, [])
                ]
            return value

        devices = []
        for device in run.devices:
            checks = []
            for check in device.checks:
                item = {
                    "check": check.check,
                    "collection_status": check.collection_status.value,
                    "evaluation": check.evaluation.value,
                }
                if check.error:
                    item["error"] = check.error
                payload_model = check.facts or check.values
                if payload_model is not None:
                    payload = payload_model.model_dump(
                        mode="json",
                        exclude_none=True,
                        exclude_defaults=True,
                    )
                    payload.pop("outcome", None)
                    for technical in (
                        "collected_at",
                        "context",
                        "source_commands",
                        "evaluation_rule",
                        "recommended_action",
                        "evidence",
                    ):
                        payload.pop(technical, None)
                    summary = payload.pop("summary", None)
                    reason = payload.pop("reason_code", None)
                    metrics = payload.pop("metrics", None)
                    records = payload.pop("records", None)
                    findings = payload.pop("affected_items", None)
                    if summary:
                        item["summary"] = summary
                    if reason:
                        item["reason_code"] = reason
                    if metrics:
                        item["metrics"] = compact(metrics)
                    if records:
                        item["records"] = compact(records)
                    if findings:
                        item["findings"] = compact(findings)
                    if payload:
                        item["data"] = compact(payload)
                checks.append(item)
            devices.append(compact({
                "device": {
                    "id": device.device_id,
                    "name": device.device_name,
                    "address": device.management_ip,
                    "platform": device.platform,
                },
                "summary": device.summary.model_dump(mode="json"),
                "checks": checks,
            }))
        return compact({
            "schema_version": 1,
            "document_type": "atlas_prophylaxis_report_data",
            "run": {
                "id": str(run.run_id),
                "operation": run.operation,
                "status": run.status,
                "started_at": (
                    run.started_at.isoformat() if run.started_at else None
                ),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "summary": run.summary.model_dump(mode="json"),
                "raw_reports": [
                    report.model_dump(mode="json")
                    for report in run.raw_reports
                ],
            },
            "devices": devices,
        })

    @staticmethod
    def _result_detail(result) -> str:
        if result.error:
            return result.error
        if result.facts is not None:
            values = result.facts.model_dump()
            return ", ".join(
                (
                    f"uptime={format_uptime_seconds(value)}"
                    if field == "uptime_seconds"
                    else f"{field}={value}"
                )
                for field, value in values.items()
                if value is not None
            )
        if result.values is not None:
            if isinstance(result.values, FirmwareRecommendationValues):
                suggested = ",".join(result.values.suggested_releases) or "none"
                return (
                    f"pid={result.values.product_id}, "
                    f"installed={result.values.installed_release}, "
                    f"suggested={suggested}, "
                    f"reason={result.values.reason_code}"
                )
            if isinstance(result.values, EolStatusValues):
                milestones = []
                for record in result.values.records:
                    milestones.append(
                        f"{record.scope}:{record.product_id or '-'}:"
                        f"eos={record.end_of_sale_date or '-'}:"
                        f"ldos={record.last_date_of_support or '-'}"
                    )
                return (
                    f"pid={result.values.product_id}, "
                    f"serial={result.values.serial_number}, "
                    f"hardware={result.values.hardware_state}, "
                    f"software={result.values.software_state}, "
                    f"hardware_records={result.values.hardware_record_count}, "
                    f"software_records={result.values.software_record_count}, "
                    f"records_truncated={result.values.records_truncated}, "
                    f"reason={result.values.reason_code}, "
                    f"milestones={'|'.join(milestones) or 'none'}"
                )
            values = result.values.model_dump()
            labels = {
                "current_percent": "current",
                "one_minute_percent": "one_minute",
                "five_minute_percent": "five_minutes",
            }
            detail = []
            for field, value in values.items():
                if value is None or field == "outcome":
                    continue
                if field == "sensors" and value:
                    for sensor in value:
                        sensor_outcome = sensor["outcome"]
                        if hasattr(sensor_outcome, "value"):
                            sensor_outcome = sensor_outcome.value
                        detail.append(
                            f"fan_{sensor['index']}={sensor['status_text']}"
                            f"/{sensor_outcome}"
                        )
                    continue
                if field == "records":
                    for record in value:
                        identity = (
                            record.get("hostname")
                            or record.get("name")
                            or record.get("serial_number")
                            or record.get("record_type")
                            or "record"
                        )
                        record_fields = ",".join(
                            f"{record_field}={record_value}"
                            for record_field, record_value in record.items()
                            if record_field != "record_type"
                            and record_value is not None
                            and record_value != identity
                        )
                        detail.append(f"{identity}[{record_fields}]")
                    continue
                label = labels.get(field, field)
                if field == "uptime_seconds":
                    detail.append(f"uptime={format_uptime_seconds(value)}")
                elif field.endswith("_percent"):
                    detail.append(f"{label}={value:g}%")
                else:
                    detail.append(f"{label}={value}")
            return ", ".join(detail) or "collected"
        return "collected"

    @staticmethod
    def _report(run: ProphylaxisRun) -> str:
        lines = [
            "Aricoma Atlas Profylaxia report",
            f"Run ID: {run.run_id}",
            f"Operation: {run.operation}",
            f"Status: {run.status}",
            f"Created: {run.created_at.isoformat()}",
            f"Started: {run.started_at.isoformat() if run.started_at else '-'}",
            f"Finished: {run.finished_at.isoformat() if run.finished_at else '-'}",
            "",
            f"Success: {run.summary.success}",
            f"Warning: {run.summary.warning}",
            f"Unknown: {run.summary.unknown}",
            f"Failure: {run.summary.failure}",
            f"Skipped: {run.summary.skipped}",
            f"Total: {run.summary.total}",
        ]
        if run.error:
            lines.extend(("", f"Run error: {run.error}"))
        if run.raw_reports:
            lines.extend(("", "Cisco raw collection artifacts:"))
            for report in run.raw_reports:
                lines.append(
                    f"- {report.tenant}/{report.site}: {report.artifact} "
                    f"({report.device_count} devices)"
                )
        devices: dict[tuple, list] = {}
        for result in run.results:
            key = (
                result.device_id,
                result.device_name,
                result.management_ip,
                result.platform,
            )
            devices.setdefault(key, []).append(result)
        charts_by_device: dict[tuple[int, str], list] = {}
        for chart in run.charts:
            charts_by_device.setdefault(
                (chart.device_id, chart.device_name),
                [],
            ).append(chart)

        inventory_checks = {
            CheckId.DEVICE_FACTS.value,
            CheckId.OS_VERSION.value,
            CheckId.SERIAL_INVENTORY.value,
            CheckId.MODEL_INVENTORY.value,
        }
        section_names = (
            "Vyžaduje zásah",
            "Naplánovať",
            "Zdravie",
            "Inventár",
            "Nepodporované/neoverené",
        )

        for key, results in devices.items():
            device_id, device_name, management_ip, platform = key
            address = management_ip or "unknown"
            lines.extend((
                "",
                f"Zariadenie: {device_name} "
                f"(id={device_id or '-'}, ip={address}, "
                f"platform={platform or 'unknown'})",
            ))
            sections = {name: [] for name in section_names}
            for result in results:
                if (
                    result.evaluation == EvaluationOutcome.FAIL
                    or result.collection_status == CollectionStatus.FAILED
                ):
                    section = "Vyžaduje zásah"
                elif result.evaluation == EvaluationOutcome.WARNING:
                    section = "Naplánovať"
                elif (
                    result.collection_status == CollectionStatus.UNSUPPORTED
                    or result.evaluation in {
                        EvaluationOutcome.UNKNOWN,
                        EvaluationOutcome.UNSUPPORTED,
                        EvaluationOutcome.NOT_APPLICABLE,
                    }
                ):
                    section = "Nepodporované/neoverené"
                elif result.check in inventory_checks:
                    section = "Inventár"
                else:
                    section = "Zdravie"
                detail = FileRunStore._result_detail(result)
                sections[section].append(
                    f"  - {result.check}: collection="
                    f"{result.collection_status.value}, evaluation="
                    f"{result.evaluation.value}, status={result.status} ({detail})"
                )
            for section in section_names:
                entries = sections[section] or ("  - bez záznamu",)
                lines.extend((f"  {section}:", *entries))

            device_charts = charts_by_device.get((device_id, device_name), ())
            if device_charts:
                lines.append("  Zabbix grafy (best effort):")
                for chart in device_charts:
                    detail = chart.artifact or chart.error or "unavailable"
                    lines.append(
                        f"  - {chart.check} [{chart.window}]: "
                        f"{chart.status} ({detail})"
                    )
        return "\n".join(lines) + "\n"


__all__ = ["FileRunStore", "RunNotFoundError", "RunStoreError"]
