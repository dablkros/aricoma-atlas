"""Human and JSON inventory import reporting."""

import json
from pathlib import Path

from atlas.inventory.models import ImportReport, ImportStatus


def render_text(report: ImportReport) -> str:
    data = report.as_dict()
    lines = ["ARICOMA ATLAS - INVENTORY IMPORT", ""]
    if len(data["tenants"]) == 1:
        lines.append(f"Tenant: {next(iter(data['tenants']))}")
        lines.append("")
    elif data["tenants"]:
        lines.append("Tenants: " + ", ".join(sorted(data["tenants"])))
        lines.append("")
    lines.append("Sites:")
    if data["sites"]:
        lines.extend(f"  {name}: {count} devices" for name, count in sorted(data["sites"].items()))
    else:
        lines.append("  none")
    lines.extend(["", f"Total rows: {data['summary']['total_rows']}"])
    lines.extend(
        f"{status.value.title():<12}{data['summary'][status.value]}"
        for status in ImportStatus
    )
    lines.extend(["", "Rows:"])
    for row in report.rows:
        detail = f" ({row.reason})" if row.reason else ""
        lines.append(f"  {row.row}: {row.hostname or '-'} [{row.status.value}] {row.operation}{detail}")
    lines.extend(["", "RESULT: " + (
        "DRY RUN - NO CHANGES MADE" if report.mode == "DRY_RUN" else "APPLY COMPLETED"
    )])
    return "\n".join(lines)


def write_json(report: ImportReport, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
