"""Shared NetBox/OpenBao to Oxidized inventory reconciliation."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import List, Optional, Tuple

from atlas.device_credentials import DeviceValidationError, resolve_oxidized_device
from atlas.deployment import write_private
from atlas.oxidized_inventory import (
    InventoryDiff,
    InventoryError,
    canonical_inventory,
    inventory_diff,
    read_inventory,
    write_inventory,
)
from atlas.services.errors import DependencyError


logger = logging.getLogger("atlas.oxidized.sync")


class OxidizedSyncFailure(RuntimeError):
    """Global sync failure safe to translate to the public API contract."""

    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class SyncLock:
    """Replaceable single-process lock for the current beta architecture."""

    def __init__(self) -> None:
        self._lock = Lock()

    def acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()


@dataclass(frozen=True)
class SyncIssue:
    device: Optional[str]
    code: str
    category: str = field(repr=False)


@dataclass(frozen=True)
class SyncSummary:
    total: int
    added: int
    updated: int
    removed: int
    unchanged: int
    skipped: int
    errors: int


@dataclass(frozen=True)
class SyncResult:
    status: str
    inventory_changed: bool
    inventory_updated: bool
    oxidized_reloaded: bool
    summary: SyncSummary
    added: Tuple[str, ...]
    updated: Tuple[str, ...]
    removed: Tuple[str, ...]
    issues: Tuple[SyncIssue, ...]


@dataclass(frozen=True)
class ReconciliationPlan:
    desired: Tuple[dict, ...] = field(repr=False)
    diff: InventoryDiff
    summary: SyncSummary
    issues: Tuple[SyncIssue, ...]


class FileInventoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.reload_marker = self.path.with_name(f".{self.path.name}.reload-required")

    def read(self) -> List[dict]:
        return list(read_inventory(self.path).entries)

    def write(self, inventory: List[dict]) -> None:
        write_inventory(self.path, inventory)

    def reload_required(self) -> bool:
        try:
            if self.reload_marker.is_symlink() or (
                self.reload_marker.exists() and not self.reload_marker.is_file()
            ):
                raise InventoryError("unsafe_path")
            return self.reload_marker.exists()
        except OSError:
            raise InventoryError("unreadable") from None

    def mark_reload_required(self) -> None:
        try:
            group_id = self.path.parent.stat().st_gid
            write_private(
                self.reload_marker,
                "pending\n",
                mode=0o640,
                group_id=group_id,
            )
        except OSError:
            raise InventoryError("write_failed") from None

    def clear_reload_required(self) -> None:
        try:
            self.reload_marker.unlink(missing_ok=True)
        except OSError:
            raise InventoryError("write_failed") from None


def resolved_inventory_entry(resolved) -> dict:
    reference = resolved.reference
    credentials = resolved.credentials
    entry = {
        "name": reference.name,
        "ip": reference.address,
        "model": reference.model,
        "username": credentials["username"],
        "password": credentials["password"],
    }
    if credentials.get("enable_password"):
        entry["enable"] = credentials["enable_password"]
    return entry


class OxidizedSyncService:
    def __init__(
        self,
        netbox,
        openbao,
        oxidized,
        inventory_store: FileInventoryStore,
        sync_lock: Optional[SyncLock] = None,
    ) -> None:
        self.netbox = netbox
        self.openbao = openbao
        self.oxidized = oxidized
        self.inventory_store = inventory_store
        self.sync_lock = sync_lock or SyncLock()

    @staticmethod
    def _device_name(device: dict) -> Optional[str]:
        name = device.get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None

    @classmethod
    def _device_label(cls, device: dict) -> str:
        name = cls._device_name(device)
        if name:
            return name
        identifier = device.get("id")
        return f"id:{identifier}" if identifier is not None else "<unnamed>"

    @staticmethod
    def _global_dependency_failure(system: str, exc: DependencyError):
        if system == "netbox":
            code = (
                "netbox_authentication_failed"
                if exc.reason == "authentication_failed"
                else "netbox_unavailable"
            )
        else:
            code = (
                "openbao_authentication_failed"
                if exc.reason == "authentication_failed"
                else "openbao_unavailable"
            )
        raise OxidizedSyncFailure(code) from exc

    def build_plan(self, current: List[dict]) -> ReconciliationPlan:
        current = canonical_inventory(current)
        current_by_name = {entry["name"]: entry for entry in current}
        try:
            devices = self.netbox.get_oxidized_devices()
        except DependencyError as exc:
            self._global_dependency_failure("netbox", exc)

        logger.info(
            "netbox_devices_loaded",
            extra={"event": "netbox_devices_loaded", "device_count": len(devices)},
        )
        desired_by_name = {}
        seen_names = set()
        protected_names = set()
        issues: List[SyncIssue] = []
        removals_safe = True

        for device in devices:
            name = self._device_name(device)
            label = self._device_label(device)
            if name is None:
                removals_safe = False
            elif name in seen_names:
                issue = SyncIssue(name, "duplicate_name", "skipped")
                issues.append(issue)
                if name in current_by_name:
                    desired_by_name[name] = current_by_name[name]
                    protected_names.add(name)
                else:
                    desired_by_name.pop(name, None)
                logger.warning(
                    "device_resolution_failed",
                    extra={
                        "event": "device_resolution_failed",
                        "device": name,
                        "reason_code": issue.code,
                    },
                )
                continue
            if name:
                seen_names.add(name)

            try:
                resolved = resolve_oxidized_device(device, self.openbao)
            except DeviceValidationError as exc:
                issue = SyncIssue(label, exc.code, exc.category)
                issues.append(issue)
                if name and name in current_by_name:
                    desired_by_name[name] = current_by_name[name]
                    protected_names.add(name)
                logger.warning(
                    "device_resolution_failed",
                    extra={
                        "event": "device_resolution_failed",
                        "device": label,
                        "reason_code": exc.code,
                    },
                )
                continue
            except DependencyError as exc:
                self._global_dependency_failure("openbao", exc)

            if resolved is None:
                issue = SyncIssue(label, "invalid_oxidized_enabled", "skipped")
                issues.append(issue)
                if name and name in current_by_name:
                    desired_by_name[name] = current_by_name[name]
                    protected_names.add(name)
                logger.warning(
                    "device_resolution_failed",
                    extra={
                        "event": "device_resolution_failed",
                        "device": label,
                        "reason_code": issue.code,
                    },
                )
                continue

            entry = resolved_inventory_entry(resolved)
            desired_by_name[entry["name"]] = entry

        if not removals_safe:
            for name, entry in current_by_name.items():
                desired_by_name.setdefault(name, entry)

        desired = canonical_inventory(desired_by_name.values())
        diff = inventory_diff(current, desired)
        skipped = sum(issue.category == "skipped" for issue in issues)
        errors = sum(issue.category == "error" for issue in issues)
        unchanged = len(set(diff.unchanged) - protected_names)
        total = (
            len(diff.added)
            + len(diff.updated)
            + len(diff.removed)
            + unchanged
            + skipped
            + errors
        )
        summary = SyncSummary(
            total=total,
            added=len(diff.added),
            updated=len(diff.updated),
            removed=len(diff.removed),
            unchanged=unchanged,
            skipped=skipped,
            errors=errors,
        )
        issues.sort(key=lambda issue: (issue.device or "", issue.code))
        return ReconciliationPlan(tuple(desired), diff, summary, tuple(issues))

    @staticmethod
    def _result(
        plan: ReconciliationPlan,
        *,
        status: str,
        inventory_updated: bool,
        oxidized_reloaded: bool,
        issues: Optional[Tuple[SyncIssue, ...]] = None,
    ) -> SyncResult:
        return SyncResult(
            status=status,
            inventory_changed=plan.diff.changed,
            inventory_updated=inventory_updated,
            oxidized_reloaded=oxidized_reloaded,
            summary=plan.summary,
            added=plan.diff.added,
            updated=plan.diff.updated,
            removed=plan.diff.removed,
            issues=plan.issues if issues is None else issues,
        )

    def sync(self) -> SyncResult:
        if not self.sync_lock.acquire():
            raise OxidizedSyncFailure("sync_already_running", status_code=409)

        logger.info("oxidized_sync_started", extra={"event": "oxidized_sync_started"})
        try:
            try:
                current = self.inventory_store.read()
                reload_required = self.inventory_store.reload_required()
            except InventoryError as exc:
                raise OxidizedSyncFailure(
                    "inventory_invalid",
                    status_code=500,
                ) from exc

            plan = self.build_plan(current)
            logger.info(
                "inventory_diff",
                extra={
                    "event": "inventory_diff",
                    "added_count": plan.summary.added,
                    "updated_count": plan.summary.updated,
                    "removed_count": plan.summary.removed,
                    "unchanged_count": plan.summary.unchanged,
                },
            )

            base_status = "partial_success" if plan.issues else "success"
            if not plan.diff.changed:
                if reload_required:
                    try:
                        self.oxidized.reload_inventory(current)
                    except DependencyError as exc:
                        reload_issue = SyncIssue(
                            None,
                            "oxidized_reload_failed",
                            "error",
                        )
                        logger.error(
                            "oxidized_reload_failed",
                            extra={
                                "event": "oxidized_reload_failed",
                                "reason_code": exc.reason,
                            },
                        )
                        logger.info(
                            "oxidized_sync_completed",
                            extra={
                                "event": "oxidized_sync_completed",
                                "sync_status": "error",
                                "inventory_changed": False,
                            },
                        )
                        return self._result(
                            plan,
                            status="error",
                            inventory_updated=False,
                            oxidized_reloaded=False,
                            issues=plan.issues + (reload_issue,),
                        )
                    try:
                        self.inventory_store.clear_reload_required()
                    except InventoryError as exc:
                        raise OxidizedSyncFailure(
                            "inventory_write_failed",
                            status_code=500,
                        ) from exc
                    logger.info(
                        "oxidized_reload_succeeded",
                        extra={"event": "oxidized_reload_succeeded"},
                    )
                    result = self._result(
                        plan,
                        status=base_status,
                        inventory_updated=False,
                        oxidized_reloaded=True,
                    )
                    logger.info(
                        "oxidized_sync_completed",
                        extra={
                            "event": "oxidized_sync_completed",
                            "sync_status": result.status,
                            "inventory_changed": False,
                        },
                    )
                    return result
                result = self._result(
                    plan,
                    status=base_status,
                    inventory_updated=False,
                    oxidized_reloaded=False,
                )
                logger.info(
                    "oxidized_sync_completed",
                    extra={
                        "event": "oxidized_sync_completed",
                        "sync_status": result.status,
                        "inventory_changed": False,
                    },
                )
                return result

            try:
                self.inventory_store.mark_reload_required()
                self.inventory_store.write(list(plan.desired))
            except (InventoryError, OSError) as exc:
                raise OxidizedSyncFailure(
                    "inventory_write_failed",
                    status_code=500,
                ) from exc

            logger.info(
                "inventory_written",
                extra={
                    "event": "inventory_written",
                    "device_count": len(plan.desired),
                },
            )
            try:
                self.oxidized.reload_inventory(list(plan.desired))
            except DependencyError as exc:
                reload_issue = SyncIssue(None, "oxidized_reload_failed", "error")
                logger.error(
                    "oxidized_reload_failed",
                    extra={
                        "event": "oxidized_reload_failed",
                        "reason_code": exc.reason,
                    },
                )
                logger.info(
                    "oxidized_sync_completed",
                    extra={
                        "event": "oxidized_sync_completed",
                        "sync_status": "error",
                        "inventory_changed": True,
                    },
                )
                return self._result(
                    plan,
                    status="error",
                    inventory_updated=True,
                    oxidized_reloaded=False,
                    issues=plan.issues + (reload_issue,),
                )

            logger.info(
                "oxidized_reload_succeeded",
                extra={"event": "oxidized_reload_succeeded"},
            )
            try:
                self.inventory_store.clear_reload_required()
            except InventoryError as exc:
                raise OxidizedSyncFailure(
                    "inventory_write_failed",
                    status_code=500,
                ) from exc
            result = self._result(
                plan,
                status=base_status,
                inventory_updated=True,
                oxidized_reloaded=True,
            )
            logger.info(
                "oxidized_sync_completed",
                extra={
                    "event": "oxidized_sync_completed",
                    "sync_status": result.status,
                    "inventory_changed": True,
                },
            )
            return result
        finally:
            self.sync_lock.release()


__all__ = [
    "FileInventoryStore",
    "OxidizedSyncFailure",
    "OxidizedSyncService",
    "ReconciliationPlan",
    "SyncIssue",
    "SyncLock",
    "SyncResult",
    "SyncSummary",
    "resolved_inventory_entry",
]
