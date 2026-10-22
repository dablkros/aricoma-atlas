#!/usr/bin/env python3
"""Persist or extend the Atlas optional-component selection."""

import argparse
import os
from pathlib import Path
import pwd
import grp
import sys

import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.components import (  # noqa: E402
    OPTIONAL_COMPONENTS,
    component_document,
    load_components,
)
from atlas.deployment import write_private  # noqa: E402


def parse_selection(value: str) -> set[str]:
    normalized = value.strip().lower()
    if normalized in {"", "none"}:
        return set()
    selected = {item.strip() for item in normalized.split(",") if item.strip()}
    unknown = selected - set(OPTIONAL_COMPONENTS)
    if unknown:
        raise ValueError(
            "Unknown optional components: " + ", ".join(sorted(unknown))
        )
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--components")
    parser.add_argument("--target-root", type=Path, default=ROOT)
    parser.add_argument("--owner", default="atlas")
    parser.add_argument("--group", default="atlas")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.show:
        selection = load_components(args.target_root / ".runtime/components.yaml")
        print("Core components: netbox, openbao, ansible, prophylaxis")
        print(
            "Optional components: "
            + (", ".join(sorted(selection.optional)) or "none")
        )
        return
    if args.components is None:
        raise ValueError("--components is required unless --show is used")
    selected = parse_selection(args.components)
    target = args.target_root.resolve() / ".runtime/components.yaml"
    if target.is_symlink():
        raise RuntimeError("Component manifest must not be a symlink")
    if target.exists():
        existing = set(load_components(target).optional)
        removed = existing - selected
        if removed:
            raise RuntimeError(
                "Installed components cannot be removed: "
                + ", ".join(sorted(removed))
            )
        if selected == existing:
            print("[OK] Component selection is unchanged")
            return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = yaml.safe_dump(component_document(selected), sort_keys=False)
    gid = grp.getgrnam(args.group).gr_gid
    write_private(target, content, mode=0o600, group_id=gid)
    os.chown(target, pwd.getpwnam(args.owner).pw_uid, gid)
    print("[OK] Optional components: " + (", ".join(sorted(selected)) or "none"))
    print(f"[OK] Wrote protected component manifest: {target}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"[ERROR] {exc}")
