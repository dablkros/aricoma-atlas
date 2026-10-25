"""Read-only Device Type discovery across NetBox and Atlas catalog sources."""

from dataclasses import asdict, dataclass
from pathlib import Path
import unicodedata

import yaml


def normalized_identifier(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


@dataclass(frozen=True)
class DeviceTypeCandidate:
    source: str
    manufacturer: str
    model: str
    slug: str
    path: str | None = None
    object_id: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _matches(query: str, *, model: object, slug: object, filename: object = "") -> bool:
    wanted = normalized_identifier(query)
    return wanted in {
        normalized_identifier(model),
        normalized_identifier(slug),
        normalized_identifier(filename),
    }


def find_netbox_candidates(client, query: str) -> list[DeviceTypeCandidate]:
    matches = []
    for item in client.get_all("/api/dcim/device-types/"):
        if not isinstance(item, dict) or not _matches(
            query,
            model=item.get("model"),
            slug=item.get("slug"),
        ):
            continue
        manufacturer = item.get("manufacturer")
        manufacturer_name = (
            manufacturer.get("name", "") if isinstance(manufacturer, dict) else ""
        )
        identifier = item.get("id")
        matches.append(DeviceTypeCandidate(
            source="NETBOX",
            manufacturer=str(manufacturer_name),
            model=str(item.get("model", "")),
            slug=str(item.get("slug", "")),
            object_id=identifier if isinstance(identifier, int) else None,
        ))
    return matches


def find_yaml_candidates(
    root: Path,
    query: str,
    *,
    source: str,
) -> tuple[list[DeviceTypeCandidate], list[str]]:
    matches, errors = find_yaml_candidates_for_queries(
        root,
        [query],
        source=source,
    )
    return matches.get(normalized_identifier(query), []), errors


def find_yaml_candidates_for_queries(
    root: Path,
    queries: list[str],
    *,
    source: str,
) -> tuple[dict[str, list[DeviceTypeCandidate]], list[str]]:
    wanted = {normalized_identifier(query) for query in queries}
    matches = {query: [] for query in wanted}
    if not wanted:
        return matches, []
    if not root.is_dir():
        return matches, [f"source_directory_missing:{root}"]
    errors = []
    paths = sorted({*root.rglob("*.yaml"), *root.rglob("*.yml")})
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{path}:{type(exc).__name__}")
            continue
        if not isinstance(data, dict):
            continue
        identifiers = {
            normalized_identifier(data.get("model")),
            normalized_identifier(data.get("slug")),
            normalized_identifier(path.stem),
        }
        candidate = DeviceTypeCandidate(
            source=source,
            manufacturer=str(data.get("manufacturer", "")),
            model=str(data.get("model", "")),
            slug=str(data.get("slug", "")),
            path=str(path),
        )
        for query in wanted & identifiers:
            matches[query].append(candidate)
    return matches, errors


def recommended_action(
    netbox: list[DeviceTypeCandidate],
    catalog: list[DeviceTypeCandidate],
    upstream: list[DeviceTypeCandidate],
) -> str:
    if any(len(values) > 1 for values in (netbox, catalog, upstream)):
        return "CONFLICT"
    if catalog:
        return "RECONCILE" if netbox else "ADD_FROM_ATLAS_CATALOG"
    if netbox:
        return "ALREADY_IN_NETBOX"
    if upstream:
        return "CATALOG_REVIEW_REQUIRED"
    return "NOT_FOUND"


__all__ = [
    "DeviceTypeCandidate",
    "find_netbox_candidates",
    "find_yaml_candidates",
    "find_yaml_candidates_for_queries",
    "normalized_identifier",
    "recommended_action",
]
