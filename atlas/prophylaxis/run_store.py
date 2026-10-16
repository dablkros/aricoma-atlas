"""Atomic, restricted local storage for Profylaxia run artifacts."""

import json
import os
from pathlib import Path
import threading
from uuid import UUID, uuid4

from atlas.prophylaxis.run_models import ProphylaxisRun


class RunStoreError(RuntimeError):
    code = "prophylaxis_run_store_unavailable"


class RunNotFoundError(RuntimeError):
    code = "prophylaxis_run_not_found"


class FileRunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
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
        return run

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
            self._atomic_write(directory / "report.txt", self._report(run))

    def get(self, run_id: UUID) -> ProphylaxisRun:
        with self._lock:
            path = self._directory(run_id) / "result.json"
            try:
                if path.is_symlink() or not path.is_file():
                    raise RunNotFoundError()
                payload = json.loads(path.read_text(encoding="utf-8"))
                return ProphylaxisRun.model_validate(payload)
            except RunNotFoundError:
                raise
            except (OSError, ValueError, json.JSONDecodeError):
                raise RunStoreError() from None

    @staticmethod
    def _result_detail(result) -> str:
        if result.error:
            return result.error
        if result.facts is not None:
            values = result.facts.model_dump()
            return ", ".join(
                f"{field}={value}"
                for field, value in values.items()
                if value is not None
            )
        if result.values is not None:
            values = result.values.model_dump()
            labels = {
                "current_percent": "current",
                "one_minute_percent": "one_minute",
                "five_minute_percent": "five_minutes",
            }
            detail = []
            for field, value in values.items():
                if value is None:
                    continue
                label = labels.get(field, field)
                if field.endswith("_percent"):
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
            f"Failure: {run.summary.failure}",
            f"Skipped: {run.summary.skipped}",
            f"Total: {run.summary.total}",
        ]
        if run.error:
            lines.extend(("", f"Run error: {run.error}"))
        if run.results:
            lines.extend(("", "Checks:"))
            for result in run.results:
                detail = FileRunStore._result_detail(result)
                lines.append(
                    f"- {result.device_name} [{result.platform or 'unknown'}] "
                    f"{result.check}: "
                    f"{result.status} ({detail})"
                )
        return "\n".join(lines) + "\n"


__all__ = ["FileRunStore", "RunNotFoundError", "RunStoreError"]
