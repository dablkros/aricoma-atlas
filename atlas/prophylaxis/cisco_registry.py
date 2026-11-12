"""Validated Cisco command and parser registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


REGISTRY_SCHEMA_VERSION = 1
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "catalog"
    / "prophylaxis"
    / "cisco_commands.yaml"
)


class CiscoParserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: Literal["ntc_textfsm", "atlas_custom", "none"]
    platform: str | None = Field(default=None, min_length=1, max_length=64)
    template: str | None = Field(default=None, min_length=1, max_length=255)
    function: str | None = Field(default=None, min_length=1, max_length=128)
    output_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_engine_contract(self):
        if self.engine == "ntc_textfsm":
            if not self.platform or not self.template or self.function:
                raise ValueError("ntc_textfsm requires platform and template only")
            if not self.template.endswith(".textfsm"):
                raise ValueError("ntc_textfsm template must end with .textfsm")
        elif self.engine == "atlas_custom":
            if not self.function or self.platform or self.template:
                raise ValueError("atlas_custom requires function only")
        elif self.platform or self.template or self.function or self.output_fields:
            raise ValueError("none parser cannot declare parser implementation")
        if len(set(self.output_fields)) != len(self.output_fields):
            raise ValueError("parser output_fields must be unique")
        return self


class CiscoVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    documentation: Literal["verified", "unverified"] = "unverified"
    parser_fixture_tested: bool = False
    real_device_tested: bool = False
    tested_models: tuple[str, ...] = ()
    tested_os_versions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


class CiscoCompatibilityCondition(BaseModel):
    """Reserved explicit constraints; no automatic version inference is performed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_version: str | None = Field(default=None, min_length=1, max_length=64)
    max_version: str | None = Field(default=None, min_length=1, max_length=64)
    models: tuple[str, ...] = ()


class CiscoCommandVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platforms: tuple[str, ...] = Field(min_length=1)
    command: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    parser: CiscoParserSpec
    verification: CiscoVerification = CiscoVerification()
    compatibility: CiscoCompatibilityCondition | None = None

    @model_validator(mode="after")
    def validate_platforms(self):
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("variant platforms must be unique")
        return self


class CiscoCommandDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    category: Literal[
        "device_identity",
        "hardware_inventory",
        "interfaces",
        "interface_errors",
        "transceivers",
        "cpu",
        "memory",
        "environment",
        "filesystems",
        "time",
        "vlans",
        "spanning_tree",
        "etherchannel",
        "licensing",
        "routing",
        "neighbors",
        "ha_status",
        "configuration",
        "logs",
    ]
    priority: int = Field(ge=1, le=999)
    modes: tuple[Literal["identity", "full_collection"], ...] = Field(min_length=1)
    variants: tuple[CiscoCommandVariant, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_definition(self):
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("command modes must be unique")
        platforms = [
            platform
            for variant in self.variants
            for platform in variant.platforms
        ]
        if len(set(platforms)) != len(platforms):
            raise ValueError("command id has multiple variants for one platform")
        return self


class CiscoCommandCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    ntc_templates_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    platforms: tuple[str, ...] = Field(min_length=1)
    commands: tuple[CiscoCommandDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self):
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("catalog platforms must be unique")
        ids = [definition.id for definition in self.commands]
        if len(set(ids)) != len(ids):
            raise ValueError("command ids must be unique")
        supported = set(self.platforms)
        pairs: set[tuple[str, str]] = set()
        for definition in self.commands:
            for variant in definition.variants:
                unknown = set(variant.platforms) - supported
                if unknown:
                    raise ValueError(
                        f"command {definition.id} uses unknown platforms: {sorted(unknown)}"
                    )
                for platform in variant.platforms:
                    pair = (platform, variant.command)
                    if variant.enabled and pair in pairs:
                        raise ValueError(
                            f"duplicate enabled command for {platform}: {variant.command}"
                        )
                    if variant.enabled:
                        pairs.add(pair)
        return self


@dataclass(frozen=True, slots=True)
class ResolvedCiscoCommand:
    id: str
    category: str
    priority: int
    command: str
    platform: str
    parser: CiscoParserSpec
    verification: CiscoVerification
    compatibility: CiscoCompatibilityCondition | None


class CiscoCommandRegistry:
    def __init__(self, catalog: CiscoCommandCatalog):
        self.catalog = catalog
        self.platforms = frozenset(catalog.platforms)

    def resolve(
        self, platform: str, mode: str,
    ) -> tuple[ResolvedCiscoCommand, ...]:
        if platform not in self.platforms:
            return ()
        resolved: list[ResolvedCiscoCommand] = []
        for definition in self.catalog.commands:
            if mode not in definition.modes:
                continue
            for variant in definition.variants:
                if platform not in variant.platforms or not variant.enabled:
                    continue
                resolved.append(ResolvedCiscoCommand(
                    id=definition.id,
                    category=definition.category,
                    priority=definition.priority,
                    command=variant.command,
                    platform=platform,
                    parser=variant.parser,
                    verification=variant.verification,
                    compatibility=variant.compatibility,
                ))
        return tuple(sorted(resolved, key=lambda item: (item.priority, item.id)))

    def command(
        self, platform: str, command: str,
    ) -> ResolvedCiscoCommand | None:
        for mode in ("identity", "full_collection"):
            for candidate in self.resolve(platform, mode):
                if candidate.command == command:
                    return candidate
        return None

    def all_commands(self, mode: str) -> tuple[str, ...]:
        commands = {
            item.command
            for platform in self.platforms
            for item in self.resolve(platform, mode)
        }
        return tuple(sorted(commands))


def load_cisco_command_registry(
    path: str | Path = DEFAULT_REGISTRY_PATH,
) -> CiscoCommandRegistry:
    registry_path = Path(path)
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Cisco command registry must be a YAML mapping")
    catalog = CiscoCommandCatalog.model_validate(payload)
    return CiscoCommandRegistry(catalog)


DEFAULT_CISCO_COMMAND_REGISTRY = load_cisco_command_registry()


__all__ = [
    "CiscoCommandCatalog",
    "CiscoCommandRegistry",
    "CiscoParserSpec",
    "DEFAULT_CISCO_COMMAND_REGISTRY",
    "DEFAULT_REGISTRY_PATH",
    "REGISTRY_SCHEMA_VERSION",
    "ResolvedCiscoCommand",
    "load_cisco_command_registry",
]
