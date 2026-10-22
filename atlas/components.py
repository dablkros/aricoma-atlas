"""Validated Atlas component selection shared by installers and runtime code."""

from dataclasses import dataclass
import os
from pathlib import Path

import yaml

from atlas.deployment import ROOT


CORE_COMPONENTS = ("netbox", "openbao", "ansible", "prophylaxis")
OPTIONAL_COMPONENTS = ("oxidized", "zabbix")
DEFAULT_COMPONENTS_FILE = ROOT / ".runtime/components.yaml"


@dataclass(frozen=True)
class ComponentSelection:
    optional: frozenset[str]

    def enabled(self, name: str) -> bool:
        if name in CORE_COMPONENTS:
            return True
        if name not in OPTIONAL_COMPONENTS:
            raise ValueError(f"Unknown Atlas component: {name}")
        return name in self.optional

    @property
    def ingress_services(self) -> tuple[str, ...]:
        return ("netbox",) + tuple(
            name for name in OPTIONAL_COMPONENTS if name in self.optional
        )


def load_components(path: Path | str | None = None) -> ComponentSelection:
    """Load component selection; missing manifests preserve the legacy full stack."""
    configured = path or os.environ.get("ATLAS_COMPONENTS_FILE")
    manifest = Path(configured) if configured else DEFAULT_COMPONENTS_FILE
    if not manifest.is_file():
        return ComponentSelection(frozenset(OPTIONAL_COMPONENTS))
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Component manifest version must be 1")
    optional = data.get("optional")
    if not isinstance(optional, dict):
        raise ValueError("Component manifest optional must be a mapping")
    unknown = set(optional) - set(OPTIONAL_COMPONENTS)
    if unknown:
        raise ValueError(
            "Component manifest contains unsupported components: "
            + ", ".join(sorted(unknown))
        )
    if any(type(value) is not bool for value in optional.values()):
        raise ValueError("Component manifest values must be true or false")
    return ComponentSelection(
        frozenset(name for name, enabled in optional.items() if enabled)
    )


def component_document(enabled: set[str] | frozenset[str]) -> dict:
    unknown = set(enabled) - set(OPTIONAL_COMPONENTS)
    if unknown:
        raise ValueError(f"Unknown optional components: {', '.join(sorted(unknown))}")
    return {
        "version": 1,
        "optional": {name: name in enabled for name in OPTIONAL_COMPONENTS},
    }


__all__ = [
    "CORE_COMPONENTS",
    "OPTIONAL_COMPONENTS",
    "ComponentSelection",
    "component_document",
    "load_components",
]
