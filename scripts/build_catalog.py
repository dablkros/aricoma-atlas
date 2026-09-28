#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "catalog" / "manifest.yaml"
LIBRARY = ROOT / "devicetype-library"
BUILD = ROOT / "build" / "device-types"


def get_git_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def collect_patterns(config):
    """
    Recursively collect all entries under 'include:'.

    Supports both:

    Fortinet:
      mode: all
      include:
        - "*.yaml"

    and:

    Cisco:
      small_business:
        include:
          - "CBS*.yaml"

      enterprise:
        include:
          - "C9300*.yaml"
    """
    patterns = []

    if isinstance(config, dict):
        include = config.get("include")

        if isinstance(include, list):
            patterns.extend(include)

        for key, value in config.items():
            if key == "include":
                continue

            if isinstance(value, (dict, list)):
                patterns.extend(collect_patterns(value))

    elif isinstance(config, list):
        for item in config:
            patterns.extend(collect_patterns(item))

    return patterns


def main():
    if not MANIFEST.exists():
        sys.exit(f"[ERROR] Manifest not found: {MANIFEST}")

    if not LIBRARY.exists():
        sys.exit(f"[ERROR] Device Type Library not found: {LIBRARY}")

    with MANIFEST.open("r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    expected_commit = manifest["upstream"]["commit"]
    actual_commit = get_git_commit(LIBRARY)

    print("Atlas Device Catalog Builder")
    print("=" * 60)

    print(f"Expected upstream commit: {expected_commit}")
    print(f"Actual upstream commit:   {actual_commit}")

    if actual_commit != expected_commit:
        sys.exit(
            "\n[ERROR] Device Type Library commit does not match manifest."
        )

    print("[OK] Upstream commit verified")
    print()

    vendors = manifest.get("vendors", {})

    if BUILD.exists():
        shutil.rmtree(BUILD)

    BUILD.mkdir(parents=True, exist_ok=True)

    report = {
        "catalog": manifest.get("catalog", {}),
        "upstream_commit": actual_commit,
        "vendors": {},
        "total": 0,
    }

    total = 0

    for vendor, config in vendors.items():
        source_dir = LIBRARY / "device-types" / vendor

        if not source_dir.exists():
            print(f"[ERROR] Vendor directory does not exist: {vendor}")
            sys.exit(1)

        patterns = collect_patterns(config)

        if not patterns:
            print(f"[ERROR] No include patterns defined for {vendor}")
            sys.exit(1)

        selected = set()

        for pattern in patterns:
            matches = list(source_dir.glob(pattern))

            if not matches:
                print(
                    f"[WARNING] {vendor}: pattern matched nothing: {pattern}"
                )

            for match in matches:
                if match.is_file() and match.suffix == ".yaml":
                    selected.add(match)

        destination = BUILD / vendor
        destination.mkdir(parents=True, exist_ok=True)

        for source in sorted(selected):
            shutil.copy2(source, destination / source.name)

        count = len(selected)
        total += count

        report["vendors"][vendor] = {
            "count": count,
            "patterns": patterns,
        }

        print(f"[OK] {vendor:<20} {count:>5} device types")

    report["total"] = total

    report_path = ROOT / "build" / "catalog-report.json"

    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print()
    print("=" * 60)
    print(f"Total device types: {total}")
    print(f"Build directory:    {BUILD}")
    print(f"Report:             {report_path}")
    print("[OK] Catalog build completed")


if __name__ == "__main__":
    main()
