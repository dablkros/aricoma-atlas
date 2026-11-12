#!/usr/bin/env python3
"""Render the validated Cisco command registry without duplicating it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.prophylaxis.cisco_registry import DEFAULT_CISCO_COMMAND_REGISTRY


def rows():
    registry = DEFAULT_CISCO_COMMAND_REGISTRY
    for platform in sorted(registry.platforms):
        for item in registry.resolve(platform, "full_collection"):
            verification = item.verification
            yield {
                "platform": platform,
                "command_id": item.id,
                "command": item.command,
                "parser": (
                    item.parser.template
                    or item.parser.function
                    or "none"
                ),
                "output_fields": list(item.parser.output_fields),
                "documentation": verification.documentation,
                "fixture_tested": verification.parser_fixture_tested,
                "real_device_tested": verification.real_device_tested,
            }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    arguments = parser.parse_args()
    payload = list(rows())
    if arguments.format == "json":
        print(json.dumps(payload, indent=2))
        return
    print("| Platform | Command | Parser | Output fields | Verification |")
    print("|---|---|---|---|---|")
    for item in payload:
        status = (
            "device-tested" if item["real_device_tested"]
            else "fixture-tested" if item["fixture_tested"]
            else item["documentation"]
        )
        print(
            f"| {item['platform']} | `{item['command']}` | "
            f"`{item['parser']}` | {', '.join(item['output_fields']) or '-'} | "
            f"{status} |"
        )


if __name__ == "__main__":
    main()
