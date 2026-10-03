"""Declarative catalog enrichment and service mapping; no runtime OS guessing."""

from collections import Counter
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parent.parent
MAPPING_FILE = ROOT / "catalog" / "platforms.yaml"


def mapping_digest():
    return sha256(MAPPING_FILE.read_bytes()).hexdigest()


def load_platform_config(path=MAPPING_FILE):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Invalid platform mapping version")
    platforms = config.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError("Missing platform definitions")
    for slug, definition in platforms.items():
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError("Invalid platform slug")
        if not isinstance(definition, dict) or not definition.get("name") or not definition.get("manufacturer"):
            raise ValueError(f"Invalid platform definition: {slug}")
    for vendor, rules in config["platform_mappings"].items():
        for rule in rules:
            if rule.get("platform") not in platforms:
                raise ValueError(f"Unknown platform in rules for {vendor}")
            if platforms[rule["platform"]]["manufacturer"] != vendor:
                raise ValueError(f"Platform manufacturer mismatch for {vendor}")
            if set(rule) - {"platform", "models", "slugs"}:
                raise ValueError(f"Unknown mapping fields for {vendor}")
            patterns = rule.get("models", []) + rule.get("slugs", [])
            if not patterns or any(not isinstance(p, str) or not p for p in patterns):
                raise ValueError(f"Invalid patterns for {vendor}")
    for slug, value in config["oxidized_platform_mapping"].items():
        if slug not in platforms or not re.fullmatch(r"[a-z0-9_]+", value.get("model", "")):
            raise ValueError("Invalid Oxidized mapping")
    return config


def default_platform(data, config):
    """Build-time only. An unknown type stays unset; conflicting rules fail."""
    matches = set()
    for rule in config["platform_mappings"].get(data.get("manufacturer"), []):
        if any(fnmatchcase(str(data.get(field, "")), pattern)
               for field, key in (("model", "models"), ("slug", "slugs"))
               for pattern in rule.get(key, [])):
            matches.add(rule["platform"])
    if len(matches) > 1:
        raise ValueError(f"Conflicting platform rules for {data.get('slug')}")
    return next(iter(matches), None)


def catalog_platform_report(items, config):
    counts = Counter()
    unmapped = []
    for data in items:
        slug = default_platform(data, config)
        if slug:
            counts[slug] += 1
        else:
            unmapped.append({key: data[key] for key in ("manufacturer", "model", "slug")})
    return {"mapped": sum(counts.values()), "unmapped": len(unmapped),
            "by_platform": dict(sorted(counts.items())), "unmapped_device_types": unmapped}


def print_platform_report(report):
    print(f"Platform mapped: {report['mapped']}; unmapped: {report['unmapped']}")
    for slug, count in report["by_platform"].items():
        print(f"  {slug}: {count}")
    print("Unmapped Device Types:")
    for item in report["unmapped_device_types"]:
        print(f"  {item['manufacturer']} / {item['model']} ({item['slug']})")


def provision_platforms(client, manufacturers, config, apply_changes):
    endpoint = "/api/dcim/platforms/"
    existing = {obj["slug"]: obj for obj in client.get_all(endpoint)}
    required = config["platforms"]
    missing = set(required) - set(existing)
    print(f"Platforms: create: {len(missing)}; existing: {len(required) - len(missing)}")
    for slug, definition in required.items():
        manufacturer = manufacturers.get(definition["manufacturer"])
        if slug in existing:
            actual = existing[slug].get("manufacturer")
            actual_id = actual.get("id") if isinstance(actual, dict) else actual
            # A generic platform (manufacturer=None) is valid in NetBox.
            if actual_id is not None and (not manufacturer or actual_id != manufacturer["id"]):
                raise ValueError(f"Platform manufacturer conflict: {slug}")
        elif apply_changes:
            if not manufacturer:
                raise ValueError(f"Missing manufacturer for platform {slug}")
            existing[slug] = client.post(endpoint, {
                "slug": slug, "name": definition["name"], "manufacturer": manufacturer["id"],
            })
    return existing


def enrich_device_types(client, items, index, platforms, config, apply_changes):
    """Only patch DeviceType.default_platform. Never modify Devices or other fields."""
    changed = 0
    for item in items:
        slug = default_platform(item["data"], config)
        obj = index.get((item["manufacturer"], item["model"]))
        if not obj:
            continue  # New type planned during dry-run.
        current = obj.get("default_platform")
        current_id = current.get("id") if isinstance(current, dict) else current
        desired = platforms.get(slug) if slug else None
        desired_id = desired["id"] if desired else None
        if slug and not desired and apply_changes:
            raise ValueError(f"Missing platform: {slug}")
        if current_id != desired_id or (slug and not desired):
            changed += 1
            print(f"  [DEFAULT PLATFORM] {item['model']} -> {slug or 'unset'}")
            if apply_changes:
                client.patch(f"/api/dcim/device-types/{obj['id']}/", {"default_platform": desired_id})
    print(f"Device Type default_platform updates: {changed}")
    return changed
