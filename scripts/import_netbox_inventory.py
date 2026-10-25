#!/usr/bin/env python3
"""Manage NetBox tenants/sites and import Atlas device inventory."""

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.inventory.config import load_inventory_config  # noqa: E402
from atlas.inventory.device_types import (  # noqa: E402
    find_netbox_candidates,
    find_yaml_candidates,
    normalized_identifier,
    recommended_action,
)
from atlas.inventory.importer import (  # noqa: E402
    InventoryConflict,
    InventoryImporter,
    InventoryInvalid,
)
from atlas.inventory.parser import parse_inventory  # noqa: E402
from atlas.inventory.report import render_text, write_json  # noqa: E402
from atlas.openbao_client import OpenBaoClient  # noqa: E402
from atlas.services.openbao import OpenBaoService  # noqa: E402
from scripts import bootstrap_netbox, provision_netbox  # noqa: E402
from scripts.provision_netbox import NetBoxClient  # noqa: E402


RUNTIME_CONFIG = ROOT / ".runtime/inventory.yaml"
OPENBAO_IDENTITY = ROOT / ".runtime/openbao-backend.json"


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
    mode_arguments(tenant_add, default_apply=True)

    site = commands.add_parser("site", help="Manage customer sites")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_add = site_commands.add_parser("add", help="Create or reuse a site")
    site_add.add_argument("--tenant", required=True)
    site_add.add_argument("--name", required=True)
    mode_arguments(site_add, default_apply=True)

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
    if not tenant or tenant.get("provision") is not True:
        print("[INFO] Default tenant was not configured; tenant provisioning skipped")
        return 0
    resolved, operation = importer.ensure_tenant(
        tenant["name"], apply=apply, slug=tenant["slug"]
    )
    print(f"[{operation}] Tenant {tenant['name']} ({tenant['slug']})")
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
        "action": recommended_action(netbox, catalog, upstream),
        "sources": {
            "netbox": [candidate.as_dict() for candidate in netbox],
            "atlas_catalog": [candidate.as_dict() for candidate in catalog],
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
        "warnings": (catalog_errors + upstream_errors)[:20],
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
    if not matches:
        if state["sources"]["netbox"]:
            print("[UNCHANGED] Device Type already exists in NetBox")
            return 0
        if state["sources"]["pinned_upstream"]:
            raise InventoryInvalid(
                "Device Type exists only in pinned upstream; add it to the Atlas "
                "manifest or reviewed local catalog and rebuild before provisioning"
            )
        raise InventoryInvalid("Device Type was not found")

    if not state["catalog"]["current"]:
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
    importer = InventoryImporter(client, config)
    apply = apply_selected(args)

    if args.command == "tenant":
        tenant, operation = importer.ensure_tenant(args.name, apply=apply)
        slug = tenant.get("slug") if tenant else "planned"
        print(f"[{operation}] Tenant {args.name} ({slug})")
        return 0

    if args.command == "site":
        tenant = importer.resolve_tenant(args.tenant)
        site, operation = importer.ensure_site(tenant, args.name, apply=apply)
        slug = site.get("slug") if site else "planned"
        print(f"[{operation}] Site {args.name} ({slug}); tenant={tenant['slug']}")
        return 0

    if args.command == "provision-defaults":
        return provision_defaults(importer, config, apply=apply)

    rows = parse_inventory(caller_path(args.file))
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
    except (InventoryConflict, InventoryInvalid, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"[ERROR] {exc}")
