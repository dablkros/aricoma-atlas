#!/usr/bin/env python3
"""Manage NetBox tenants/sites and import Atlas device inventory."""

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.inventory.config import load_inventory_config  # noqa: E402
from atlas.inventory.importer import (  # noqa: E402
    InventoryConflict,
    InventoryImporter,
    InventoryInvalid,
)
from atlas.inventory.parser import parse_inventory  # noqa: E402
from atlas.inventory.report import render_text, write_json  # noqa: E402
from atlas.openbao_client import OpenBaoClient  # noqa: E402
from atlas.services.openbao import OpenBaoService  # noqa: E402
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


def main() -> int:
    args = parse_args()
    config = load_inventory_config(RUNTIME_CONFIG)
    importer = InventoryImporter(netbox_client(), config)
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
