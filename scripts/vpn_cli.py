#!/usr/bin/env python3
"""Inspect and validate the P1 VPN Connection Registry."""

import argparse
import json

from atlas.config import get_settings
from atlas.services.factory import build_dependency_services
from atlas.vpn.service import VPNRegistryError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="List registered connections")
    listing.add_argument("--tenant-id", type=int)
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)

    show = commands.add_parser("show", help="Show one connection")
    show.add_argument("connection_id")

    resolve = commands.add_parser("resolve", help="Resolve one NetBox device")
    resolve.add_argument("--device-id", type=int, required=True)
    resolve.add_argument("--tenant-id", type=int)

    validate = commands.add_parser(
        "validate", help="Validate NetBox ownership and OpenBao secret reference"
    )
    validate.add_argument("connection_id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = build_dependency_services(get_settings()).vpn
    if args.command == "list":
        result = service.list(
            tenant_id=args.tenant_id, limit=args.limit, offset=args.offset,
        ).model_dump(mode="json")
    elif args.command == "show":
        result = service.get(args.connection_id).model_dump(mode="json")
    elif args.command == "resolve":
        result = service.resolve_device(
            args.device_id, expected_tenant=args.tenant_id,
        )
    else:
        result = service.validate(args.connection_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VPNRegistryError as exc:
        print(json.dumps({"error": {"code": exc.code}}, sort_keys=True))
        raise SystemExit(2) from None
