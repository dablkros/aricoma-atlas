"""Canonical, secret-safe Oxidized JSONFile inventory operations."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from atlas.deployment import write_private


REQUIRED_FIELDS = {"name", "ip", "model", "username", "password"}
ALLOWED_FIELDS = REQUIRED_FIELDS | {"enable"}


class InventoryError(RuntimeError):
    """Inventory failure identified by a safe machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InventorySnapshot:
    state: str
    entries: Tuple[dict, ...]


@dataclass(frozen=True)
class InventoryDiff:
    added: Tuple[str, ...]
    updated: Tuple[str, ...]
    removed: Tuple[str, ...]
    unchanged: Tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def validate_inventory(data: object) -> List[dict]:
    if not isinstance(data, list):
        raise InventoryError("wrong_schema")

    validated: List[dict] = []
    names = set()
    for entry in data:
        if (
            not isinstance(entry, dict)
            or set(entry) - ALLOWED_FIELDS
            or not REQUIRED_FIELDS.issubset(entry)
        ):
            raise InventoryError("wrong_schema")
        if any(
            not isinstance(entry[field], str) or not entry[field]
            for field in REQUIRED_FIELDS
        ):
            raise InventoryError("wrong_schema")
        if "enable" in entry and (
            not isinstance(entry["enable"], str) or not entry["enable"]
        ):
            raise InventoryError("wrong_schema")
        if entry["name"] in names:
            raise InventoryError("duplicate_name")
        names.add(entry["name"])
        validated.append(dict(entry))
    return validated


def canonical_inventory(entries: Iterable[dict]) -> List[dict]:
    if isinstance(entries, (str, bytes, dict)):
        raise InventoryError("wrong_schema")
    return sorted(validate_inventory(list(entries)), key=lambda item: item["name"])


def render_inventory(entries: Iterable[dict]) -> str:
    inventory = canonical_inventory(entries)
    rendered = json.dumps(
        inventory,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"
    validate_inventory(json.loads(rendered))
    return rendered


def read_inventory(path: Path) -> InventorySnapshot:
    path = Path(path)
    try:
        if path.is_symlink():
            raise InventoryError("unsafe_path")
        if not path.exists():
            return InventorySnapshot("missing", ())
        if not path.is_file():
            raise InventoryError("unsafe_path")
    except OSError:
        raise InventoryError("unreadable") from None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise InventoryError("unreadable") from None
    if not content.strip():
        raise InventoryError("empty_file")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise InventoryError("invalid_json") from None
    entries = canonical_inventory(data)
    return InventorySnapshot("empty" if not entries else "valid", tuple(entries))


def write_inventory(path: Path, entries: Iterable[dict]) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise InventoryError("write_failed") from None
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise InventoryError("unsafe_path")
        group_id = (
            path.stat().st_gid if path.exists() else path.parent.stat().st_gid
        )
        write_private(
            path,
            render_inventory(entries),
            mode=0o640,
            group_id=group_id,
        )
    except InventoryError:
        raise
    except OSError:
        raise InventoryError("write_failed") from None


def inventory_diff(current: Iterable[dict], desired: Iterable[dict]) -> InventoryDiff:
    current_by_name: Dict[str, dict] = {
        item["name"]: item for item in canonical_inventory(current)
    }
    desired_by_name: Dict[str, dict] = {
        item["name"]: item for item in canonical_inventory(desired)
    }
    current_names = set(current_by_name)
    desired_names = set(desired_by_name)
    shared = current_names & desired_names
    return InventoryDiff(
        added=tuple(sorted(desired_names - current_names)),
        updated=tuple(
            sorted(
                name
                for name in shared
                if current_by_name[name] != desired_by_name[name]
            )
        ),
        removed=tuple(sorted(current_names - desired_names)),
        unchanged=tuple(
            sorted(
                name
                for name in shared
                if current_by_name[name] == desired_by_name[name]
            )
        ),
    )
