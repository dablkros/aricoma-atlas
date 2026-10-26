"""Typed inventory import contracts."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ImportStatus(StrEnum):
    CREATE = "CREATE"
    UNCHANGED = "UNCHANGED"
    INVALID = "INVALID"
    CONFLICT = "CONFLICT"
    ERROR = "ERROR"


@dataclass(frozen=True)
class InventoryRow:
    number: int
    hostname: str
    model: str
    management_ip: str
    tenant: str | None = None
    site: str | None = None
    device_role: str | None = None
    platform: str | None = None
    vrf: str | None = None


@dataclass
class RowResult:
    row: int
    hostname: str
    model: str
    management_ip: str
    tenant: str | None
    site: str | None
    operation: str
    status: ImportStatus
    reason: str | None = None
    object_ids: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class ImportReport:
    mode: str
    rows: list[RowResult]

    def as_dict(self) -> dict[str, Any]:
        counts = {status.value: 0 for status in ImportStatus}
        tenants: dict[str, int] = {}
        sites: dict[str, int] = {}
        for row in self.rows:
            counts[row.status.value] += 1
            if row.tenant:
                tenants[row.tenant] = tenants.get(row.tenant, 0) + 1
            if row.site:
                sites[row.site] = sites.get(row.site, 0) + 1
        return {
            "schema_version": 1,
            "mode": self.mode,
            "summary": {"total_rows": len(self.rows), **counts},
            "tenants": tenants,
            "sites": sites,
            "rows": [row.as_dict() for row in self.rows],
        }
