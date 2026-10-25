"""Authorized browser inventory planning, YAML staging and confirmed apply."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, Request
import yaml

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator
from atlas.api.schemas.inventory import DeviceTypeUploadRequest, InventoryTableRequest
from atlas.inventory.config import load_inventory_config
from atlas.inventory.importer import InventoryConflict, InventoryImporter, InventoryInvalid
from atlas.inventory.parser import parse_inventory_table
from atlas.services.errors import DependencyError
from atlas.platforms import load_platform_config
from scripts import provision_netbox
from scripts.import_netbox_inventory import _device_type_item, prepare_import_device_types


router = APIRouter(prefix="/inventory", tags=["inventory"])
CONFIG = Path(os.environ.get("ATLAS_INVENTORY_CONFIG", "/run/atlas/inventory/config.yaml"))
DEVICE_TYPES = Path(os.environ.get("ATLAS_INVENTORY_DEVICE_TYPES", "/run/atlas/inventory/device-types"))
TRUST_MOUNTED_CATALOG = os.environ.get("ATLAS_INVENTORY_TRUST_MOUNTED_CATALOG") == "true"


def authorized(request: Request) -> None:
    require_operator(request, scope="inventory")


def _hash(body: InventoryTableRequest) -> str:
    canonical = json.dumps(
        {"table": body.table, "tenant": body.tenant, "site": body.site},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _execute(body: InventoryTableRequest, request: Request, *, apply: bool) -> dict:
    try:
        rows = parse_inventory_table(body.table)
        if not rows:
            raise ValueError("Inventory table contains no device rows")
        config = load_inventory_config(CONFIG)
        client = request.app.state.dependencies.netbox
        planned, errors = prepare_import_device_types(
            client,
            rows,
            config,
            apply=apply,
            local_device_type_dir=DEVICE_TYPES,
            trusted_catalog_report=TRUST_MOUNTED_CATALOG,
        )
        importer = InventoryImporter(
            client, config, planned_device_types=planned, device_type_errors=errors,
        )
        report = importer.import_rows(
            rows, tenant_override=body.tenant, site_override=body.site, apply=apply,
        ).as_dict()
    except (ValueError, InventoryInvalid, InventoryConflict) as exc:
        raise AtlasError(422, "inventory_invalid", str(exc)) from None
    except DependencyError:
        raise AtlasError(503, "inventory_dependency_unavailable", "Inventory dependency is unavailable") from None
    blocked = sum(report["summary"][key] for key in ("INVALID", "CONFLICT", "ERROR"))
    issue_by_model = {}
    for row in report["rows"]:
        reason = row.get("reason") or ""
        if not reason.startswith("device_type_"):
            continue
        issue_by_model.setdefault(row["model"], reason)
    device_type_issues = [
        {
            "model": model,
            "reason": reason,
            "yaml_upload_allowed": reason == "device_type_not_found_upload_yaml_to_runtime_catalog",
        }
        for model, reason in sorted(issue_by_model.items())
    ]
    return {
        "plan_hash": _hash(body),
        "apply_allowed": blocked == 0 and report["summary"]["total_rows"] > 0,
        "missing_device_types": [issue["model"] for issue in device_type_issues],
        "device_type_issues": device_type_issues,
        "report": report,
    }


@router.post("/import/plan", dependencies=[Depends(authorized)])
def plan(body: InventoryTableRequest, request: Request) -> dict:
    return _execute(body, request, apply=False)


@router.post("/import/apply", dependencies=[Depends(authorized)])
def apply(body: InventoryTableRequest, request: Request) -> dict:
    expected = _hash(body)
    if body.plan_hash != expected:
        raise AtlasError(409, "inventory_plan_stale", "Inventory input changed; create a new plan")
    preview = _execute(body, request, apply=False)
    if not preview["apply_allowed"]:
        raise AtlasError(409, "inventory_plan_blocked", "Resolve all inventory plan issues before apply")
    return _execute(body, request, apply=True)


@router.post("/device-types/upload", dependencies=[Depends(authorized)])
def upload_device_type(body: DeviceTypeUploadRequest) -> dict:
    try:
        data = yaml.safe_load(body.yaml_content)
        if not isinstance(data, dict):
            raise ValueError("YAML root must be an object")
        for required in ("manufacturer", "model", "slug"):
            if not isinstance(data.get(required), str) or not data[required].strip():
                raise ValueError(f"{required} is required")
        item = _device_type_item(data, Path(body.filename), load_platform_config())
        errors = provision_netbox.validate_item(item)
        if errors:
            raise ValueError("; ".join(errors))
    except (ValueError, yaml.YAMLError) as exc:
        raise AtlasError(422, "device_type_yaml_invalid", str(exc)) from None
    if DEVICE_TYPES.is_symlink():
        raise AtlasError(409, "device_type_directory_invalid", "Device Type directory is invalid")
    DEVICE_TYPES.mkdir(parents=True, exist_ok=True)
    target = DEVICE_TYPES / body.filename
    if target.exists() and not body.replace:
        raise AtlasError(409, "device_type_yaml_exists", "Device Type YAML already exists")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=DEVICE_TYPES, prefix=".upload-", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body.yaml_content)
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"status": "stored", "filename": body.filename, "model": item["model"],
            "part_number": item["data"].get("part_number")}


__all__ = ["router"]
