"""Authorized browser inventory planning, YAML staging and confirmed apply."""

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, Request
import yaml

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator
from atlas.api.schemas.inventory import (
    DeviceTypeUploadRequest,
    InventoryTableRequest,
    SiteRequest,
    TenantRequest,
)
from atlas.inventory.config import load_inventory_config
from atlas.inventory.importer import (
    InventoryConflict, InventoryImporter, InventoryInvalid, related_id, slugify,
)
from atlas.inventory.parser import parse_inventory_table
from atlas.services.errors import DependencyError
from atlas.vpn.service import VPNRegistryError
from atlas.platforms import load_platform_config
from scripts import provision_netbox
from scripts.import_netbox_inventory import _device_type_item, prepare_import_device_types


router = APIRouter(prefix="/inventory", tags=["inventory"])
CONFIG = Path(os.environ.get("ATLAS_INVENTORY_CONFIG", "/run/atlas/inventory/config.yaml"))
DEVICE_TYPES = Path(os.environ.get("ATLAS_INVENTORY_DEVICE_TYPES", "/run/atlas/inventory/device-types"))
TRUST_MOUNTED_CATALOG = os.environ.get("ATLAS_INVENTORY_TRUST_MOUNTED_CATALOG") == "true"
logger = logging.getLogger("atlas.inventory")


def _yaml_error_message(exc: yaml.YAMLError) -> str:
    """Return a stable, human-readable YAML syntax location."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None)
    description = (
        str(problem).strip()
        if isinstance(problem, str)
        else "invalid YAML syntax"
    )
    if mark is None:
        return f"Invalid Device Type YAML: {description}"
    return (
        f"Invalid Device Type YAML at line {mark.line + 1}, "
        f"column {mark.column + 1}: {description}. "
        "Check that every key starts on its own line."
    )


def authorized(request: Request) -> None:
    require_operator(request, scope="inventory")


def _hash(body: InventoryTableRequest) -> str:
    canonical = json.dumps(
        {"table": body.table, "tenant": body.tenant, "site": body.site},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _object_hash(kind: str, values: dict) -> str:
    canonical = json.dumps(
        {"kind": kind, **values}, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _importer(request: Request) -> InventoryImporter:
    return InventoryImporter(
        request.app.state.dependencies.netbox,
        load_inventory_config(CONFIG),
    )


def _inventory_error(exc: Exception) -> AtlasError:
    if isinstance(exc, DependencyError):
        return AtlasError(
            503, "inventory_dependency_unavailable", "Inventory dependency is unavailable"
        )
    return AtlasError(422, "inventory_invalid", str(exc))


@router.get("/tenants", dependencies=[Depends(authorized)])
def tenants(request: Request) -> list[dict]:
    try:
        values = request.app.state.dependencies.netbox.get_all("/api/tenancy/tenants/")
    except DependencyError as exc:
        raise _inventory_error(exc) from None
    return [
        {"id": item.get("id"), "name": item.get("name"), "slug": item.get("slug")}
        for item in values if isinstance(item, dict)
    ]


@router.get("/sites", dependencies=[Depends(authorized)])
def sites(request: Request, tenant: str) -> list[dict]:
    try:
        importer = _importer(request)
        owner = importer.resolve_tenant(tenant)
        values = request.app.state.dependencies.netbox.get_all(
            "/api/dcim/sites/", params={"tenant_id": owner["id"]},
        )
    except (InventoryInvalid, InventoryConflict, DependencyError) as exc:
        raise _inventory_error(exc) from None
    return [
        {"id": item.get("id"), "name": item.get("name"), "slug": item.get("slug")}
        for item in values if isinstance(item, dict)
        and related_id(item.get("tenant")) == owner["id"]
    ]


@router.get("/devices/{device_id}/connection", dependencies=[Depends(authorized)])
def device_connection(
    device_id: int, request: Request, tenant: str | None = None,
) -> dict:
    try:
        return request.app.state.dependencies.vpn.resolve_device(
            device_id, expected_tenant=tenant,
        )
    except VPNRegistryError as exc:
        raise AtlasError(
            exc.status_code,
            "inventory_connection_invalid",
            exc.code,
        ) from None


def _tenant_operation(body: TenantRequest, request: Request, *, apply: bool) -> dict:
    values = {
        "name": " ".join(body.name.split()),
        "slug": body.slug,
        "group": " ".join(body.group.split()) if body.group else None,
    }
    expected = _object_hash("tenant", values)
    if apply and body.plan_hash != expected:
        raise AtlasError(409, "inventory_plan_stale", "Tenant input changed; create a new plan")
    try:
        importer = _importer(request)
        tenant, tenant_operation, vrf, vrf_operation = (
            importer.ensure_tenant_with_management_vrf(
                values["name"],
                slug=values["slug"],
                group=values["group"],
                apply=apply,
            )
        )
    except (InventoryInvalid, InventoryConflict, DependencyError) as exc:
        raise _inventory_error(exc) from None
    return {
        "plan_hash": expected,
        "kind": "tenant",
        "operation": (
            "CREATE"
            if "CREATE" in {tenant_operation, vrf_operation}
            else "UNCHANGED"
        ),
        "object": tenant or values,
        "management_vrf": vrf or {
            "name": f"{values['slug'] or slugify(values['name'])}-mgmt",
        },
        "operations": {
            "tenant": tenant_operation,
            "management_vrf": vrf_operation,
        },
    }


@router.post("/tenants/plan", dependencies=[Depends(authorized)])
def tenant_plan(body: TenantRequest, request: Request) -> dict:
    return _tenant_operation(body, request, apply=False)


@router.post("/tenants/apply", dependencies=[Depends(authorized)])
def tenant_apply(body: TenantRequest, request: Request) -> dict:
    return _tenant_operation(body, request, apply=True)


def _site_operation(body: SiteRequest, request: Request, *, apply: bool) -> dict:
    values = {"tenant": body.tenant.strip(), "name": " ".join(body.name.split())}
    expected = _object_hash("site", values)
    if apply and body.plan_hash != expected:
        raise AtlasError(409, "inventory_plan_stale", "Site input changed; create a new plan")
    try:
        importer = _importer(request)
        owner = importer.resolve_tenant(values["tenant"])
        site, operation = importer.ensure_site(owner, values["name"], apply=apply)
    except (InventoryInvalid, InventoryConflict, DependencyError) as exc:
        raise _inventory_error(exc) from None
    return {
        "plan_hash": expected,
        "kind": "site",
        "operation": operation,
        "object": site or values,
    }


@router.post("/sites/plan", dependencies=[Depends(authorized)])
def site_plan(body: SiteRequest, request: Request) -> dict:
    return _site_operation(body, request, apply=False)


@router.post("/sites/apply", dependencies=[Depends(authorized)])
def site_apply(body: SiteRequest, request: Request) -> dict:
    return _site_operation(body, request, apply=True)


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
    logger.info(
        "inventory.operation_started",
        extra={
            "event": "inventory.operation_started",
            "operation": "plan",
            "input_bytes": len(body.table.encode("utf-8")),
        },
    )
    result = _execute(body, request, apply=False)
    logger.info(
        "inventory.operation_completed",
        extra={
            "event": "inventory.operation_completed",
            "operation": "plan",
            "apply_allowed": result["apply_allowed"],
            **result["report"]["summary"],
        },
    )
    return result


@router.post("/import/apply", dependencies=[Depends(authorized)])
def apply(body: InventoryTableRequest, request: Request) -> dict:
    logger.info(
        "inventory.operation_started",
        extra={
            "event": "inventory.operation_started",
            "operation": "apply",
            "input_bytes": len(body.table.encode("utf-8")),
        },
    )
    expected = _hash(body)
    if body.plan_hash != expected:
        raise AtlasError(409, "inventory_plan_stale", "Inventory input changed; create a new plan")
    preview = _execute(body, request, apply=False)
    if not preview["apply_allowed"]:
        raise AtlasError(409, "inventory_plan_blocked", "Resolve all inventory plan issues before apply")
    result = _execute(body, request, apply=True)
    logger.info(
        "inventory.operation_completed",
        extra={
            "event": "inventory.operation_completed",
            "operation": "apply",
            **result["report"]["summary"],
        },
    )
    return result


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
    except yaml.YAMLError as exc:
        raise AtlasError(
            422,
            "device_type_yaml_invalid",
            _yaml_error_message(exc),
        ) from None
    except ValueError as exc:
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
