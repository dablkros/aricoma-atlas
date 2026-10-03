#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atlas.platforms import mapping_digest

SCRIPTS = ROOT / "scripts"

MANIFEST_FILE = (
    ROOT
    / "catalog"
    / "manifest.yaml"
)

UPSTREAM_DIR = (
    ROOT
    / "devicetype-library"
)

BUILD_DIR = (
    ROOT
    / "build"
)

DEVICE_TYPE_BUILD = (
    BUILD_DIR
    / "device-types"
)

CATALOG_REPORT = (
    BUILD_DIR
    / "catalog-report.json"
)

BUILD_CATALOG = (
    SCRIPTS
    / "build_catalog.py"
)

PROVISION_BASELINE = (
    SCRIPTS
    / "provision_baseline.py"
)

PROVISION_NETBOX = (
    SCRIPTS
    / "provision_netbox.py"
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap Atlas configuration "
            "into a NetBox instance"
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the Atlas baseline and "
            "Device Catalog to NetBox"
        ),
    )

    parser.add_argument(
        "--rebuild-catalog",
        action="store_true",
        help=(
            "Force synchronization of the "
            "upstream Device Type Library "
            "and rebuild the Atlas catalog"
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help=(
            "Bulk API batch size used by "
            "Device Type provisioning "
            "(default: 200)"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Enable verbose Device Type "
            "provisioning output"
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def run_command(
    description,
    command,
):
    print()
    print(
        f"[RUN] {description}"
    )

    print(
        "      "
        + " ".join(
            str(part)
            for part in command
        )
    )

    print()

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=os.environ.copy(),
    )

    if result.returncode != 0:
        print()
        print(
            f"[ERROR] {description} "
            f"failed with exit code "
            f"{result.returncode}"
        )

        raise SystemExit(
            result.returncode
        )

    print()
    print(
        f"[OK] {description}"
    )


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_output(*args):
    result = subprocess.run(
        [
            "git",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or
            result.stdout.strip()
            or
            "Git command failed"
        )

    return result.stdout.strip()


def upstream_git_output(*args):
    result = subprocess.run(
        [
            "git",
            "-C",
            str(UPSTREAM_DIR),
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or
            result.stdout.strip()
            or
            "Upstream Git command failed"
        )

    return result.stdout.strip()


def normalize_repository_url(value):
    value = value.strip().rstrip("/")

    if value.endswith(".git"):
        value = value[:-4]

    return value


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest():
    if not MANIFEST_FILE.exists():
        raise RuntimeError(
            f"Manifest not found: "
            f"{MANIFEST_FILE}"
        )

    with MANIFEST_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:
        manifest = yaml.safe_load(
            handle
        )

    if not isinstance(
        manifest,
        dict,
    ):
        raise RuntimeError(
            "Manifest root must be "
            "a YAML object"
        )

    catalog = manifest.get(
        "catalog"
    )

    upstream = manifest.get(
        "upstream"
    )

    vendors = manifest.get(
        "vendors"
    )

    if not isinstance(
        catalog,
        dict,
    ):
        raise RuntimeError(
            "manifest.catalog is missing "
            "or invalid"
        )

    if not isinstance(
        upstream,
        dict,
    ):
        raise RuntimeError(
            "manifest.upstream is missing "
            "or invalid"
        )

    if not isinstance(
        vendors,
        dict,
    ):
        raise RuntimeError(
            "manifest.vendors is missing "
            "or invalid"
        )

    repository = upstream.get(
        "repository"
    )

    commit = upstream.get(
        "commit"
    )

    if not repository:
        raise RuntimeError(
            "manifest.upstream.repository "
            "is missing"
        )

    if not commit:
        raise RuntimeError(
            "manifest.upstream.commit "
            "is missing"
        )

    return manifest


# ---------------------------------------------------------------------------
# Manifest pattern extraction
# ---------------------------------------------------------------------------

def collect_include_patterns(node):
    """
    Recursively collect all 'include' patterns
    from a vendor configuration.

    This supports both simple vendor definitions
    and nested groups such as Cisco enterprise /
    small_business.
    """

    patterns = []

    if isinstance(
        node,
        dict,
    ):
        include = node.get(
            "include"
        )

        if isinstance(
            include,
            list,
        ):
            for pattern in include:
                patterns.append(
                    str(pattern)
                )

        for key, value in (
            node.items()
        ):
            if key == "include":
                continue

            patterns.extend(
                collect_include_patterns(
                    value
                )
            )

    elif isinstance(
        node,
        list,
    ):
        for value in node:
            patterns.extend(
                collect_include_patterns(
                    value
                )
            )

    return patterns


def expected_vendor_patterns(
    manifest,
):
    result = {}

    for vendor, config in (
        manifest["vendors"].items()
    ):
        patterns = sorted(
            set(
                collect_include_patterns(
                    config
                )
            )
        )

        result[vendor] = patterns

    return result


# ---------------------------------------------------------------------------
# Catalog report
# ---------------------------------------------------------------------------

def load_catalog_report():
    if not CATALOG_REPORT.exists():
        return None

    try:
        with CATALOG_REPORT.open(
            "r",
            encoding="utf-8",
        ) as handle:
            report = json.load(
                handle
            )

    except Exception:
        return None

    if not isinstance(
        report,
        dict,
    ):
        return None

    return report


# ---------------------------------------------------------------------------
# Existing upstream checkout inspection
# ---------------------------------------------------------------------------

def get_upstream_origin():
    if not UPSTREAM_DIR.exists():
        return None

    git_dir = (
        UPSTREAM_DIR
        / ".git"
    )

    if not git_dir.exists():
        return None

    try:
        return upstream_git_output(
            "remote",
            "get-url",
            "origin",
        )

    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Catalog freshness
# ---------------------------------------------------------------------------

def catalog_status(
    manifest,
):
    """
    Determine whether build/device-types was
    generated from the current manifest.

    Returns:
        (is_current, reasons)
    """

    reasons = []

    if not DEVICE_TYPE_BUILD.exists():
        reasons.append(
            "build/device-types does not exist"
        )

        return False, reasons

    yaml_files = list(
        DEVICE_TYPE_BUILD.glob(
            "*/*.yaml"
        )
    )

    if not yaml_files:
        reasons.append(
            "built Device Catalog is empty"
        )

        return False, reasons

    report = load_catalog_report()

    if report is None:
        reasons.append(
            "catalog-report.json is missing "
            "or invalid"
        )

        return False, reasons

    # -----------------------------------------------------------------------
    # Catalog metadata
    # -----------------------------------------------------------------------

    if report.get("platform_mapping_sha256") != mapping_digest():
        reasons.append("platform mapping changed or enrichment is missing")

    expected_catalog = (
        manifest["catalog"]
    )

    actual_catalog = (
        report.get(
            "catalog"
        )
    )

    if (
        actual_catalog
        != expected_catalog
    ):
        reasons.append(
            "catalog metadata differs "
            "from manifest"
        )

    # -----------------------------------------------------------------------
    # Upstream commit
    # -----------------------------------------------------------------------

    expected_commit = (
        manifest[
            "upstream"
        ][
            "commit"
        ]
    )

    actual_commit = (
        report.get(
            "upstream_commit"
        )
    )

    if (
        actual_commit
        != expected_commit
    ):
        reasons.append(
            "upstream commit differs "
            "from manifest"
        )

    # -----------------------------------------------------------------------
    # Upstream repository
    #
    # Current catalog-report.json does not store
    # repository URL, therefore compare the local
    # upstream checkout origin with the manifest.
    # -----------------------------------------------------------------------

    expected_repository = (
        normalize_repository_url(
            manifest[
                "upstream"
            ][
                "repository"
            ]
        )
    )

    actual_origin = (
        get_upstream_origin()
    )

    if actual_origin is None:
        reasons.append(
            "upstream repository checkout "
            "is missing or invalid"
        )

    else:
        actual_repository = (
            normalize_repository_url(
                actual_origin
            )
        )

        if (
            actual_repository
            != expected_repository
        ):
            reasons.append(
                "upstream repository differs "
                "from manifest"
            )

    # -----------------------------------------------------------------------
    # Vendor selection patterns
    # -----------------------------------------------------------------------

    expected_patterns = (
        expected_vendor_patterns(
            manifest
        )
    )

    report_vendors = report.get(
        "vendors",
        {}
    )

    if not isinstance(
        report_vendors,
        dict,
    ):
        reasons.append(
            "vendor data in catalog report "
            "is invalid"
        )

    else:
        actual_vendor_names = set(
            report_vendors.keys()
        )

        expected_vendor_names = set(
            expected_patterns.keys()
        )

        if (
            actual_vendor_names
            != expected_vendor_names
        ):
            reasons.append(
                "vendor list differs "
                "from manifest"
            )

        else:
            for vendor in sorted(
                expected_vendor_names
            ):
                report_vendor = (
                    report_vendors.get(
                        vendor,
                        {}
                    )
                )

                actual_patterns = sorted(
                    set(
                        report_vendor.get(
                            "patterns",
                            [],
                        )
                    )
                )

                if (
                    actual_patterns
                    != expected_patterns[
                        vendor
                    ]
                ):
                    reasons.append(
                        f"selection patterns "
                        f"for {vendor} differ "
                        f"from manifest"
                    )

    # -----------------------------------------------------------------------
    # Generated file count
    # -----------------------------------------------------------------------

    report_total = report.get(
        "total"
    )

    actual_total = len(
        yaml_files
    )

    if (
        report_total
        != actual_total
    ):
        reasons.append(
            "catalog file count differs "
            "from catalog report"
        )

    return (
        len(reasons) == 0,
        reasons,
    )


# ---------------------------------------------------------------------------
# Upstream synchronization
# ---------------------------------------------------------------------------

def ensure_upstream_checkout(
    manifest,
):
    repository = (
        manifest[
            "upstream"
        ][
            "repository"
        ]
    )

    commit = (
        manifest[
            "upstream"
        ][
            "commit"
        ]
    )

    print()
    print(
        "Upstream Device Type Library"
    )
    print("-" * 78)

    print(
        f"Repository: {repository}"
    )

    print(
        f"Commit:     {commit}"
    )

    # -----------------------------------------------------------------------
    # Clone if missing
    # -----------------------------------------------------------------------

    if not UPSTREAM_DIR.exists():
        print()
        print(
            "[INFO] Upstream checkout "
            "does not exist"
        )

        run_command(
            "Clone Device Type Library",
            [
                "git",
                "clone",
                repository,
                str(UPSTREAM_DIR),
            ],
        )

    # -----------------------------------------------------------------------
    # Validate repository
    # -----------------------------------------------------------------------

    if not (
        UPSTREAM_DIR
        / ".git"
    ).exists():
        raise RuntimeError(
            f"{UPSTREAM_DIR} exists but "
            f"is not a Git repository"
        )

    # -----------------------------------------------------------------------
    # Protect local changes
    # -----------------------------------------------------------------------

    dirty = upstream_git_output(
        "status",
        "--porcelain",
    )

    if dirty:
        raise RuntimeError(
            "devicetype-library contains "
            "local changes. Refusing to "
            "change repository or checkout."
        )

    # -----------------------------------------------------------------------
    # Synchronize origin
    # -----------------------------------------------------------------------

    try:
        current_origin = (
            upstream_git_output(
                "remote",
                "get-url",
                "origin",
            )
        )

    except RuntimeError:
        current_origin = None

    if current_origin is None:
        print(
            "[INFO] Adding upstream origin"
        )

        upstream_git_output(
            "remote",
            "add",
            "origin",
            repository,
        )

    elif (
        normalize_repository_url(
            current_origin
        )
        !=
        normalize_repository_url(
            repository
        )
    ):
        print(
            "[INFO] Upstream repository "
            "changed"
        )

        print(
            f"       old: {current_origin}"
        )

        print(
            f"       new: {repository}"
        )

        upstream_git_output(
            "remote",
            "set-url",
            "origin",
            repository,
        )

    # -----------------------------------------------------------------------
    # Fetch
    # -----------------------------------------------------------------------

    print(
        "[INFO] Fetching upstream repository..."
    )

    result = subprocess.run(
        [
            "git",
            "-C",
            str(UPSTREAM_DIR),
            "fetch",
            "--all",
            "--tags",
            "--prune",
        ],
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Failed to fetch upstream "
            "Device Type Library"
        )

    # -----------------------------------------------------------------------
    # Verify commit exists
    # -----------------------------------------------------------------------

    result = subprocess.run(
        [
            "git",
            "-C",
            str(UPSTREAM_DIR),
            "cat-file",
            "-e",
            f"{commit}^{{commit}}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Commit {commit} does not "
            f"exist in upstream repository"
        )

    # -----------------------------------------------------------------------
    # Checkout exact pinned commit
    # -----------------------------------------------------------------------

    print(
        "[INFO] Checking out pinned commit..."
    )

    result = subprocess.run(
        [
            "git",
            "-C",
            str(UPSTREAM_DIR),
            "checkout",
            "--detach",
            commit,
        ],
        cwd=ROOT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Failed to checkout pinned "
            "upstream commit"
        )

    actual_commit = (
        upstream_git_output(
            "rev-parse",
            "HEAD",
        )
    )

    if actual_commit != commit:
        raise RuntimeError(
            "Upstream checkout verification "
            "failed"
        )

    print(
        f"[OK] Upstream checkout: "
        f"{actual_commit}"
    )


# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------

def validate_environment():
    if not os.getenv(
        "NETBOX_TOKEN"
    ):
        raise SystemExit(
            "[ERROR] NETBOX_TOKEN "
            "environment variable is not set"
        )

    required_files = [
        MANIFEST_FILE,
        BUILD_CATALOG,
        PROVISION_BASELINE,
        PROVISION_NETBOX,
    ]

    for path in required_files:
        if not path.exists():
            raise SystemExit(
                f"[ERROR] Required file "
                f"not found: {path}"
            )


# ---------------------------------------------------------------------------
# Catalog preparation
# ---------------------------------------------------------------------------

def prepare_catalog(
    python,
    manifest,
    force_rebuild,
):
    print_header(
        "ATLAS DEVICE CATALOG"
    )

    is_current, reasons = (
        catalog_status(
            manifest
        )
    )

    if (
        force_rebuild
        or
        not is_current
    ):
        if force_rebuild:
            print(
                "[INFO] Catalog rebuild "
                "explicitly requested"
            )

        if reasons:
            print(
                "[STALE] Existing Device "
                "Catalog is not current:"
            )

            for reason in reasons:
                print(
                    f"        - {reason}"
                )

        try:
            ensure_upstream_checkout(
                manifest
            )

        except Exception as exc:
            raise SystemExit(
                f"[ERROR] Unable to prepare "
                f"upstream Device Type "
                f"Library: {exc}"
            )

        run_command(
            "Build Atlas Device Catalog",
            [
                python,
                str(BUILD_CATALOG),
            ],
        )

        # ---------------------------------------------------------------
        # Verify rebuilt catalog against manifest.
        # ---------------------------------------------------------------

        is_current, reasons = (
            catalog_status(
                manifest
            )
        )

        if not is_current:
            print()
            print(
                "[ERROR] Rebuilt catalog "
                "still does not match "
                "the manifest:"
            )

            for reason in reasons:
                print(
                    f"        - {reason}"
                )

            raise SystemExit(1)

        report = load_catalog_report()

        print()
        print(
            f"[OK] Catalog rebuilt: "
            f"{report['total']} "
            f"Device Types"
        )

        return

    report = load_catalog_report()

    print(
        f"[OK] Existing Device Catalog "
        f"is current: "
        f"{report['total']} Device Types"
    )

    print(
        f"[OK] Upstream commit: "
        f"{report['upstream_commit']}"
    )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def run_preflight(
    python,
    batch_size,
    verbose,
):
    print_header(
        "PREFLIGHT"
    )

    run_command(
        "Validate Atlas NetBox baseline",
        [
            python,
            str(PROVISION_BASELINE),
        ],
    )

    command = [
        python,
        str(PROVISION_NETBOX),
        "--all",
        "--batch-size",
        str(batch_size),
    ]

    if verbose:
        command.append(
            "--verbose"
        )

    run_command(
        "Validate Atlas Device Catalog",
        command,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_baseline(
    python,
):
    print_header(
        "APPLY ATLAS BASELINE"
    )

    run_command(
        (
            "Provision Custom Field "
            "Choice Sets and Custom Fields"
        ),
        [
            python,
            str(PROVISION_BASELINE),
            "--apply",
        ],
    )


def apply_device_catalog(
    python,
    batch_size,
    verbose,
):
    print_header(
        "APPLY ATLAS DEVICE CATALOG"
    )

    command = [
        python,
        str(PROVISION_NETBOX),
        "--apply",
        "--all",
        "--batch-size",
        str(batch_size),
    ]

    if verbose:
        command.append(
            "--verbose"
        )

    run_command(
        "Provision Atlas Device Catalog",
        command,
    )


# ---------------------------------------------------------------------------
# Final verification
# ---------------------------------------------------------------------------

def final_verification(
    python,
    batch_size,
):
    print_header(
        "FINAL VERIFICATION"
    )

    run_command(
        "Verify Atlas NetBox baseline",
        [
            python,
            str(PROVISION_BASELINE),
        ],
    )

    run_command(
        "Verify Atlas Device Catalog",
        [
            python,
            str(PROVISION_NETBOX),
            "--all",
            "--batch-size",
            str(batch_size),
        ],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if (
        args.batch_size < 1
        or
        args.batch_size > 1000
    ):
        raise SystemExit(
            "[ERROR] --batch-size must be "
            "between 1 and 1000"
        )

    validate_environment()

    try:
        manifest = load_manifest()

    except Exception as exc:
        raise SystemExit(
            f"[ERROR] Manifest validation "
            f"failed: {exc}"
        )

    python = sys.executable

    print()
    print("=" * 78)
    print("ATLAS NETBOX BOOTSTRAP")
    print("=" * 78)

    print(
        f"Mode:        "
        f"{'APPLY' if args.apply else 'DRY-RUN'}"
    )

    print(
        f"Python:      {python}"
    )

    print(
        f"NetBox URL:  "
        f"{os.getenv('NETBOX_URL', 'http://localhost:8000')}"
    )

    print(
        f"Batch size:  "
        f"{args.batch_size}"
    )

    print(
        f"Catalog:     "
        f"{manifest['catalog'].get('version')}"
    )

    print(
        f"Upstream:    "
        f"{manifest['upstream']['commit']}"
    )

    # -----------------------------------------------------------------------
    # Build / validate catalog
    # -----------------------------------------------------------------------

    prepare_catalog(
        python,
        manifest,
        args.rebuild_catalog,
    )

    # -----------------------------------------------------------------------
    # Dry-run / preflight mode
    # -----------------------------------------------------------------------

    if not args.apply:
        run_preflight(
            python,
            args.batch_size,
            args.verbose,
        )

        print_header(
            "DRY-RUN COMPLETE"
        )

        print(
            "[OK] Atlas bootstrap "
            "preflight completed."
        )

        print()
        print(
            "No changes were made to NetBox."
        )

        print()
        print(
            "To apply:"
        )

        print(
            "  python3 scripts/"
            "bootstrap_netbox.py --apply"
        )

        return

    # -----------------------------------------------------------------------
    # Apply mode starts here.
    #
    # The provisioners perform their own validation before making changes.
    # A complete remote dry-run before APPLY would duplicate the same
    # expensive NetBox API scan. Final verification remains enabled below.
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Apply baseline
    # -----------------------------------------------------------------------

    apply_baseline(
        python,
    )

    # -----------------------------------------------------------------------
    # Apply Device Catalog
    # -----------------------------------------------------------------------

    apply_device_catalog(
        python,
        args.batch_size,
        args.verbose,
    )

    # -----------------------------------------------------------------------
    # Final idempotency verification
    # -----------------------------------------------------------------------

    final_verification(
        python,
        args.batch_size,
    )

    print_header(
        "ATLAS NETBOX BOOTSTRAP COMPLETE"
    )

    print(
        "[OK] Atlas baseline and "
        "Device Catalog are provisioned."
    )

    print()

    print(
        "[OK] Final verification passed."
    )


if __name__ == "__main__":
    main()
