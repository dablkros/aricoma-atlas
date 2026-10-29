#!/usr/bin/env python3
"""Manage NetBox tenants/sites and import Atlas device inventory."""

import argparse
import json
import os
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.inventory.config import load_inventory_config, save_inventory_config  # noqa: E402
from atlas.inventory.device_types import (  # noqa: E402
    find_local_candidates,
    find_netbox_candidates,
    find_yaml_candidates,
    find_yaml_candidates_for_queries,
    normalized_identifier,
    recommended_action,
)
from atlas.inventory.importer import (  # noqa: E402
    InventoryConflict,
    InventoryImporter,
    InventoryInvalid,
    slugify,
)
from atlas.inventory.parser import parse_inventory  # noqa: E402
from atlas.inventory.report import render_text, write_json  # noqa: E402
from atlas.openbao_client import OpenBaoClient  # noqa: E402
from atlas.platforms import default_platform, load_platform_config  # noqa: E402
from atlas.services.openbao import OpenBaoService  # noqa: E402
from scripts import bootstrap_netbox, provision_netbox  # noqa: E402
from scripts.provision_netbox import NetBoxClient  # noqa: E402


RUNTIME_CONFIG = ROOT / ".runtime/inventory.yaml"
OPENBAO_IDENTITY = ROOT / ".runtime/openbao-backend.json"
LOCAL_DEVICE_TYPE_DIR = ROOT / ".runtime/inventory/device-types"


def caller_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(os.environ.get("ATLAS_CALLER_CWD", str(Path.cwd()))) / path
    return path.resolve()


def netbox_client() -> NetBoxClient:
    token = os.environ.get("NETBOX_TOKEN")
    if not token:
        openbao = OpenBaoService(
            OpenBaoClient(os.environ.get("OPENBAO_URL", "http://127.0.0.1:18200")),
            OPENBAO_IDENTITY,
        )
        token = openbao.get_netbox_api_token()
    return NetBoxClient(
        os.environ.get("NETBOX_URL", "http://127.0.0.1:8000"),
        token,
    )


def mode_arguments(parser: argparse.ArgumentParser, *, default_apply: bool) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Plan without NetBox writes")
    mode.add_argument("--apply", action="store_true", help="Apply NetBox writes")
    parser.set_defaults(default_apply=default_apply)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    tenant = commands.add_parser("tenant", help="Manage customer tenants")
    tenant_commands = tenant.add_subparsers(dest="tenant_command", required=True)
    tenant_add = tenant_commands.add_parser("add", help="Create or reuse a tenant")
    tenant_add.add_argument("--name", required=True)
    tenant_add.add_argument("--group")
    mode_arguments(tenant_add, default_apply=True)

    site = commands.add_parser("site", help="Manage customer sites")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_add = site_commands.add_parser("add", help="Create or reuse a site")
    site_add.add_argument("--tenant", required=True)
    site_add.add_argument("--name", required=True)
    mode_arguments(site_add, default_apply=True)
    site_default = site_commands.add_parser(
        "set-default",
        help="Create or reuse a site and save it as the inventory default",
    )
    site_default.add_argument("--tenant", required=True)
    site_default.add_argument("--name", required=True)
    mode_arguments(site_default, default_apply=True)

    vrf = commands.add_parser("vrf", help="Manage tenant-owned management VRFs")
    vrf_commands = vrf.add_subparsers(dest="vrf_command", required=True)
    vrf_add = vrf_commands.add_parser("add", help="Create or reuse a tenant VRF")
    vrf_add.add_argument("--tenant", required=True)
    vrf_add.add_argument("--name", required=True)
    vrf_add.add_argument(
        "--allow-overlap-within-vrf", action="store_true",
        help="Disable NetBox enforce_unique for this VRF",
    )
    mode_arguments(vrf_add, default_apply=False)

    inventory_import = commands.add_parser("import", help="Import CSV or XLSX devices")
    inventory_import.add_argument("file")
    inventory_import.add_argument("--tenant")
    inventory_import.add_argument("--site")
    inventory_import.add_argument("--vrf")
    inventory_import.add_argument("--report-json")
    mode_arguments(inventory_import, default_apply=False)

    device_type = commands.add_parser(
        "device-type",
        help="Find or reconcile a Device Type from the pinned Atlas catalog",
    )
    device_type_commands = device_type.add_subparsers(
        dest="device_type_command",
        required=True,
    )
    device_type_search = device_type_commands.add_parser(
        "search",
        help="Search NetBox, the built Atlas catalog and pinned upstream checkout",
    )
    device_type_search.add_argument("model")
    device_type_search.add_argument("--json", action="store_true")

    device_type_add = device_type_commands.add_parser(
        "add",
        help="Reconcile one Device Type already present in the Atlas catalog",
    )
    device_type_add.add_argument("model")
    device_type_add.add_argument("--verbose", action="store_true")
    mode_arguments(device_type_add, default_apply=False)

    defaults = commands.add_parser(
        "provision-defaults",
        help="Provision the configured first-boot tenant (deployment hook)",
    )
    mode_arguments(defaults, default_apply=False)
    return parser.parse_args()


def apply_selected(args: argparse.Namespace) -> bool:
    if args.apply:
        return True
    if args.dry_run:
        return False
    return bool(args.default_apply)


def provision_defaults(importer: InventoryImporter, config: dict, *, apply: bool) -> int:
    tenant = config.get("default_tenant")
    if not tenant:
        print("[INFO] Default tenant was not configured; tenant provisioning skipped")
        return 0
    if tenant.get("provision") is True:
        resolved, operation = importer.ensure_tenant(
            tenant["name"], apply=apply, slug=tenant["slug"], group=tenant.get("group")
        )
    else:
        resolved = importer.resolve_tenant(tenant["slug"])
        operation = "UNCHANGED"
    print(f"[{operation}] Tenant {tenant['name']} ({tenant['slug']})")
    site = config.get("default_site")
    if site and site.get("provision") is True:
        resolved_tenant = resolved or {
            "id": -1,
            "name": tenant["name"],
            "slug": tenant["slug"],
        }
        _resolved_site, site_operation = importer.ensure_site(
            resolved_tenant,
            site["name"],
            apply=apply,
        )
        print(f"[{site_operation}] Site {site['name']} ({site['slug']})")
    return 0


def device_type_state(client: NetBoxClient, query: str) -> dict:
    if not normalized_identifier(query):
        raise InventoryInvalid("Device Type model must not be empty")
    if len(query) > 200:
        raise InventoryInvalid("Device Type model is too long")

    manifest = bootstrap_netbox.load_manifest()
    netbox = find_netbox_candidates(client, query)
    catalog, catalog_errors = find_yaml_candidates(
        provision_netbox.CATALOG,
        query,
        source="ATLAS_CATALOG",
    )
    upstream, upstream_errors = find_yaml_candidates(
        bootstrap_netbox.UPSTREAM_DIR / "device-types",
        query,
        source="PINNED_UPSTREAM",
    )
    local, local_errors = find_local_candidates(LOCAL_DEVICE_TYPE_DIR, query)

    expected_commit = manifest["upstream"]["commit"]
    actual_commit = None
    try:
        actual_commit = bootstrap_netbox.upstream_git_output("rev-parse", "HEAD")
    except RuntimeError:
        pass

    try:
        catalog_current, catalog_reasons = bootstrap_netbox.catalog_status(manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        catalog_current = False
        catalog_reasons = [f"catalog_status_error:{type(exc).__name__}"]

    return {
        "schema_version": 1,
        "query": query,
        "action": recommended_action(netbox, catalog, upstream, local),
        "sources": {
            "netbox": [candidate.as_dict() for candidate in netbox],
            "atlas_catalog": [candidate.as_dict() for candidate in catalog],
            "atlas_local": [candidate.as_dict() for candidate in local],
            "pinned_upstream": [candidate.as_dict() for candidate in upstream],
        },
        "catalog": {
            "current": catalog_current,
            "reasons": catalog_reasons,
        },
        "upstream": {
            "repository": manifest["upstream"]["repository"],
            "expected_commit": expected_commit,
            "actual_commit": actual_commit,
            "pinned_commit_active": actual_commit == expected_commit,
        },
        "warnings": (catalog_errors + local_errors + upstream_errors)[:20],
    }


def render_device_type_state(state: dict) -> str:
    lines = [
        "Atlas Device Type lookup",
        "=" * 72,
        f"Query: {state['query']}",
    ]
    labels = (
        ("NETBOX", "netbox"),
        ("ATLAS_CATALOG", "atlas_catalog"),
        ("PINNED_UPSTREAM", "pinned_upstream"),
        ("ATLAS_LOCAL", "atlas_local"),
    )
    for label, key in labels:
        matches = state["sources"][key]
        if not matches:
            lines.append(f"{label}: MISSING")
            continue
        lines.append(f"{label}: {'CONFLICT' if len(matches) > 1 else 'FOUND'}")
        for match in matches:
            lines.append(
                f"  - {match['manufacturer']} / {match['model']} ({match['slug']})"
            )
    upstream = state["upstream"]
    lines.extend((
        f"Catalog current: {'yes' if state['catalog']['current'] else 'no'}",
        f"Pinned upstream commit: {upstream['actual_commit'] or 'unavailable'}",
        f"Action: {state['action']}",
    ))
    for reason in state["catalog"]["reasons"]:
        lines.append(f"  [CATALOG] {reason}")
    for warning in state["warnings"]:
        lines.append(f"  [WARNING] {warning}")
    if state["action"] == "NOT_FOUND":
        lines.append(f"Upload Device Type YAML to: {LOCAL_DEVICE_TYPE_DIR}")
    return "\n".join(lines)


def catalog_items_matching(query: str) -> tuple[list[dict], list[str]]:
    items, parse_errors = provision_netbox.load_catalog()
    wanted = normalized_identifier(query)
    matches = [
        item for item in items
        if wanted in {
            normalized_identifier(item["model"]),
            normalized_identifier(item["slug"]),
            normalized_identifier(item["filename"]),
        }
    ]
    return matches, parse_errors


def _inventory_model_target(model: str, config: dict) -> str:
    requested = normalized_identifier(model)
    aliases = config["device_type_aliases"]
    alias = aliases.get(model)
    if alias is None:
        alias = next((
            value for key, value in aliases.items()
            if normalized_identifier(key) == requested
        ), None)
    return str(alias or model).strip()


def _upstream_item(path: Path, platform_config: dict) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InventoryInvalid("device_type_upstream_invalid")
    return _device_type_item(data, path, platform_config)


def _device_type_item(data: dict, path: Path, platform_config: dict) -> dict:
    data = dict(data)
    data["default_platform"] = default_platform(data, platform_config)
    return {
        "file": path,
        "filename": path.stem,
        "manufacturer": str(data.get("manufacturer", "")),
        "model": str(data.get("model", "")),
        "slug": str(data.get("slug", "")),
        "data": data,
    }


def prepare_import_device_types(
    client: NetBoxClient,
    rows: list,
    config: dict,
    *,
    apply: bool,
    local_device_type_dir: Path | None = None,
    trusted_catalog_report: bool = False,
) -> tuple[list[dict], dict[str, str]]:
    if local_device_type_dir is None:
        local_device_type_dir = LOCAL_DEVICE_TYPE_DIR
    targets = {
        normalized_identifier(_inventory_model_target(row.model, config)):
        _inventory_model_target(row.model, config)
        for row in rows
        if normalized_identifier(row.model)
    }
    existing = client.get_all("/api/dcim/device-types/")
    existing_models = set()
    for item in existing:
        if not isinstance(item, dict):
            continue
        manufacturer = item.get("manufacturer")
        manufacturer_name = (
            manufacturer.get("name", "")
            if isinstance(manufacturer, dict)
            else ""
        )
        model_name = item.get("model", "")
        for identifier in (
            model_name,
            item.get("slug"),
            item.get("part_number"),
            f"{manufacturer_name} {model_name}",
        ):
            normalized = normalized_identifier(identifier)
            if normalized:
                existing_models.add(normalized)
    missing = {
        key: value for key, value in targets.items()
        if key not in existing_models
    }
    if not missing:
        return [], {}

    items, parse_errors = provision_netbox.load_catalog()
    catalog_index: dict[str, list[dict]] = {key: [] for key in missing}
    for item in items:
        identifiers = {
            normalized_identifier(item.get("model")),
            normalized_identifier(item.get("slug")),
            normalized_identifier(item.get("filename")),
            normalized_identifier(item.get("data", {}).get("part_number")),
            normalized_identifier(
                f"{str(item.get('manufacturer') or '').strip()} "
                f"{str(item.get('model') or '').strip()}"
            ),
        }
        for key in set(missing) & identifiers:
            catalog_index[key].append(item)

    errors: dict[str, str] = {}
    if parse_errors:
        errors.update({key: "device_type_catalog_invalid" for key in missing})
        return [], errors

    manifest = bootstrap_netbox.load_manifest()
    try:
        catalog_current, _reasons = bootstrap_netbox.catalog_status(
            manifest,
            require_upstream_repository=not trusted_catalog_report,
        )
    except (OSError, RuntimeError, ValueError):
        catalog_current = False
    upstream_needed = [
        key for key in missing
        if not catalog_index[key]
    ]
    upstream_index, upstream_errors = find_yaml_candidates_for_queries(
        bootstrap_netbox.UPSTREAM_DIR / "device-types",
        [missing[key] for key in upstream_needed],
        source="PINNED_UPSTREAM",
    )

    upstream_pinned = False
    if upstream_needed and not upstream_errors:
        if trusted_catalog_report:
            report = bootstrap_netbox.load_catalog_report() or {}
            upstream_pinned = (
                report.get("upstream_commit") == manifest["upstream"]["commit"]
            )
        else:
            try:
                upstream_pinned = (
                    bootstrap_netbox.upstream_git_output("rev-parse", "HEAD")
                    == manifest["upstream"]["commit"]
                )
            except RuntimeError:
                upstream_pinned = False

    local_needed = [
        key for key in upstream_needed
        if not upstream_index.get(key, [])
    ]
    local_index, local_errors = find_yaml_candidates_for_queries(
        local_device_type_dir,
        [missing[key] for key in local_needed],
        source="ATLAS_LOCAL",
    ) if local_device_type_dir.exists() else ({}, [])

    platform_config = load_platform_config()
    selected: list[dict] = []
    selected_sources: dict[str, str] = {}
    for key, display_model in missing.items():
        candidates = catalog_index[key]
        source = "ATLAS_CATALOG"
        if candidates and not catalog_current:
            errors[key] = "device_type_catalog_stale"
            continue
        if not candidates:
            candidates = list(upstream_index.get(key, []))
            source = "PINNED_UPSTREAM"
            if upstream_errors or not upstream_pinned:
                errors[key] = "device_type_upstream_unavailable"
                continue
        if not candidates:
            local_candidates = list(local_index.get(key, []))
            if len(local_candidates) > 1:
                errors[key] = "device_type_ambiguous"
                continue
            if local_candidates:
                try:
                    path = Path(local_candidates[0].path)
                    local_data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if not isinstance(local_data, dict):
                        raise ValueError("local YAML root is not an object")
                    candidates = [_device_type_item(local_data, path, platform_config)]
                    source = "ATLAS_LOCAL"
                except (OSError, ValueError, yaml.YAMLError):
                    errors[key] = "device_type_local_invalid"
                    continue
            if local_errors:
                errors[key] = "device_type_local_invalid"
                continue
        if not candidates:
            errors[key] = "device_type_not_found_upload_yaml_to_runtime_catalog"
            print(
                f"[DEVICE TYPE] {display_model}: NOT_FOUND; "
                f"upload YAML to {local_device_type_dir}"
            )
            continue
        if len(candidates) != 1:
            errors[key] = "device_type_ambiguous"
            continue
        try:
            item = candidates[0]
            if source == "PINNED_UPSTREAM":
                item = _upstream_item(Path(item.path), platform_config)
            validation_errors = provision_netbox.validate_item(item)
        except (OSError, ValueError, yaml.YAMLError):
            errors[key] = "device_type_upstream_invalid"
            continue
        if validation_errors:
            errors[key] = "device_type_validation_failed"
            continue
        selected.append(item)
        selected_sources[str(item["file"])] = source
        print(f"[DEVICE TYPE] {display_model}: {source} -> PLAN_CREATE")

    if not selected:
        return [], errors

    provision_netbox.provision(
        client,
        selected,
        apply,
        provision_netbox.DEFAULT_BATCH_SIZE,
        False,
    )
    if apply:
        return [], errors

    platforms = {
        str(item.get("slug", "")): item
        for item in client.get_all("/api/dcim/platforms/")
        if isinstance(item, dict)
    }
    planned = []
    for offset, item in enumerate(selected, start=1):
        slug = default_platform(item["data"], platform_config)
        planned.append({
            "id": -offset,
            "model": item["model"],
            "slug": item["slug"],
            "part_number": item["data"].get("part_number", ""),
            "manufacturer": {"name": item["manufacturer"]},
            "default_platform": platforms.get(slug) if slug else None,
            "atlas_source": selected_sources[str(item["file"])],
        })
    return planned, errors


def manage_device_type(args: argparse.Namespace, client: NetBoxClient) -> int:
    state = device_type_state(client, args.model)
    if args.device_type_command == "search":
        print(
            json.dumps(state, indent=2, ensure_ascii=False)
            if args.json else render_device_type_state(state)
        )
        return 2 if state["action"] in {"NOT_FOUND", "CONFLICT"} else 0

    print(render_device_type_state(state))
    if state["action"] == "CONFLICT":
        raise InventoryConflict("Device Type lookup returned multiple exact matches")

    matches, parse_errors = catalog_items_matching(args.model)
    if parse_errors:
        raise InventoryInvalid(
            f"Atlas Device Catalog contains {len(parse_errors)} parse error(s)"
        )
    if len(matches) > 1:
        raise InventoryConflict("Atlas catalog returned multiple exact matches")
    source = "ATLAS_CATALOG"
    if not matches:
        local = state["sources"]["atlas_local"]
        if state["sources"]["netbox"] and not local:
            print("[UNCHANGED] Device Type already exists in NetBox")
            return 0
        if local:
            path = Path(local[0]["path"])
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise InventoryInvalid("device_type_local_invalid")
            matches = [_device_type_item(data, path, load_platform_config())]
            source = "ATLAS_LOCAL"
        if state["sources"]["pinned_upstream"]:
            if not matches:
                raise InventoryInvalid(
                    "Device Type exists only in pinned upstream; add it to the Atlas "
                    "manifest or reviewed local catalog and rebuild before provisioning"
                )
        if not matches:
            raise InventoryInvalid("Device Type was not found")

    if source == "ATLAS_CATALOG" and not state["catalog"]["current"]:
        raise InventoryInvalid(
            "Atlas Device Catalog is stale; rebuild it with the pinned bootstrap flow"
        )

    validation_errors = provision_netbox.validate_item(matches[0])
    if validation_errors:
        raise InventoryInvalid("; ".join(validation_errors))

    apply = apply_selected(args)
    status = client.status()
    netbox_version = status.get("netbox-version", "unknown")
    print(f"NetBox: {netbox_version}")
    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    if netbox_version != provision_netbox.TESTED_NETBOX_VERSION:
        print(
            "[WARNING] Provisioner was developed against NetBox "
            f"{provision_netbox.TESTED_NETBOX_VERSION}"
        )
    provision_netbox.provision(
        client,
        matches,
        apply,
        provision_netbox.DEFAULT_BATCH_SIZE,
        args.verbose,
    )
    print(f"[OK] {'APPLY' if apply else 'DRY-RUN'} complete")
    return 0


def main() -> int:
    args = parse_args()
    client = netbox_client()
    if args.command == "device-type":
        return manage_device_type(args, client)

    config = load_inventory_config(RUNTIME_CONFIG)
    apply = apply_selected(args)

    if args.command == "tenant":
        importer = InventoryImporter(client, config)
        tenant, operation = importer.ensure_tenant(
            args.name, apply=apply, group=args.group
        )
        slug = tenant.get("slug") if tenant else "planned"
        print(f"[{operation}] Tenant {args.name} ({slug})")
        return 0

    if args.command == "site":
        importer = InventoryImporter(client, config)
        tenant = importer.resolve_tenant(args.tenant)
        site, operation = importer.ensure_site(tenant, args.name, apply=apply)
        slug = site.get("slug") if site else slugify(args.name)
        print(f"[{operation}] Site {args.name} ({slug}); tenant={tenant['slug']}")
        if args.site_command == "set-default":
            updated = dict(config)
            updated["default_tenant"] = {
                "name": tenant["name"],
                "slug": tenant["slug"],
                "provision": True,
            }
            updated["default_site"] = {
                "name": " ".join(args.name.split()),
                "slug": slug,
                "provision": True,
            }
            if apply:
                save_inventory_config(RUNTIME_CONFIG, updated)
                print(f"[OK] Default inventory Site saved: {slug}")
            else:
                print(f"[PLAN] Default inventory Site: {slug}")
        return 0

    if args.command == "vrf":
        importer = InventoryImporter(client, config)
        tenant = importer.resolve_tenant(args.tenant)
        vrf, operation = importer.ensure_vrf(
            tenant,
            args.name,
            apply=apply,
            enforce_unique=not args.allow_overlap_within_vrf,
        )
        identifier = vrf.get("id") if vrf else "planned"
        print(
            f"[{operation}] VRF {args.name} ({identifier}); "
            f"tenant={tenant['slug']}"
        )
        return 0

    if args.command == "provision-defaults":
        importer = InventoryImporter(client, config)
        return provision_defaults(importer, config, apply=apply)

    rows = parse_inventory(caller_path(args.file))
    planned_types, device_type_errors = prepare_import_device_types(
        client,
        rows,
        config,
        apply=apply,
    )
    importer = InventoryImporter(
        client,
        config,
        planned_device_types=planned_types,
        device_type_errors=device_type_errors,
    )
    report = importer.import_rows(
        rows,
        tenant_override=args.tenant,
        site_override=args.site,
        vrf_override=args.vrf,
        apply=apply,
    )
    print(render_text(report))
    if args.report_json:
        report_path = caller_path(args.report_json)
        write_json(report, report_path)
        print(f"JSON report: {report_path}")
    failed = sum(
        report.as_dict()["summary"][name]
        for name in ("INVALID", "CONFLICT", "ERROR")
    )
    return 2 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        InventoryConflict,
        InventoryInvalid,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise SystemExit(f"[ERROR] {exc}")
