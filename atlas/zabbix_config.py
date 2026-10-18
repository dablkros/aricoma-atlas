"""Validated, centralized Zabbix platform and metric mappings."""

from fnmatch import fnmatchcase
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "catalog/zabbix.yaml"
NAME = re.compile(r"[^\x00-\x1f]{1,128}")
KEY_PREFIX = re.compile(r"[A-Za-z0-9_.\[\]-]+")


@lru_cache(maxsize=1)
def load_zabbix_config(path: Path = CONFIG_FILE) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read Zabbix mapping: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != 2:
        raise ValueError("Zabbix mapping version must be 2")
    if not re.fullmatch(r"7\.0\.\d+", str(data.get("zabbix_version", ""))):
        raise ValueError("Zabbix mapping must identify an explicit 7.0 LTS release")
    group = data.get("managed_host_group")
    if not isinstance(group, str) or not NAME.fullmatch(group):
        raise ValueError("managed_host_group is invalid")
    templates = data.get("platform_templates")
    if not isinstance(templates, dict) or not templates:
        raise ValueError("platform_templates must be a non-empty object")
    for platform, template in templates.items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(platform)):
            raise ValueError("Invalid Zabbix platform slug")
        if not isinstance(template, str) or not NAME.fullmatch(template):
            raise ValueError("Invalid Zabbix template name")
    rules = data.get("device_type_template_rules") or {}
    if not isinstance(rules, dict):
        raise ValueError("device_type_template_rules must be an object")
    for platform, platform_rules in rules.items():
        if platform not in templates or not isinstance(platform_rules, list):
            raise ValueError("Invalid Zabbix device type template platform")
        seen_patterns = set()
        for rule in platform_rules:
            if not isinstance(rule, dict) or set(rule) != {
                "device_type_patterns",
                "template",
            }:
                raise ValueError("Invalid Zabbix device type template rule")
            template = rule["template"]
            patterns = rule["device_type_patterns"]
            if not isinstance(template, str) or not NAME.fullmatch(template):
                raise ValueError("Invalid Zabbix rule template name")
            if (
                not isinstance(patterns, list)
                or not patterns
                or any(
                    not isinstance(pattern, str)
                    or not re.fullmatch(r"[a-z0-9*?_-]{1,128}", pattern)
                    for pattern in patterns
                )
            ):
                raise ValueError("Invalid Zabbix device type pattern")
            if seen_patterns.intersection(patterns):
                raise ValueError("Duplicate Zabbix device type pattern")
            seen_patterns.update(patterns)
    metadata = data.get("template_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("template_metadata must be an object")
    referenced = set(templates.values()) | {
        rule["template"]
        for platform_rules in rules.values()
        for rule in platform_rules
    }
    if set(metadata) != referenced:
        raise ValueError("template_metadata must exactly cover referenced templates")
    for template, details in metadata.items():
        if not isinstance(template, str) or not NAME.fullmatch(template):
            raise ValueError("Invalid Zabbix template metadata name")
        if not isinstance(details, dict) or set(details) != {
            "origin",
            "coverage",
            "metrics",
        }:
            raise ValueError("Invalid Zabbix template metadata")
        if details["origin"] not in {"atlas", "zabbix"}:
            raise ValueError("Invalid Zabbix template origin")
        if not isinstance(details["coverage"], str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", details["coverage"]
        ):
            raise ValueError("Invalid Zabbix template coverage")
        metrics_for_template = details["metrics"]
        if (
            not isinstance(metrics_for_template, list)
            or any(
                not isinstance(metric, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]*", metric)
                for metric in metrics_for_template
            )
            or len(set(metrics_for_template)) != len(metrics_for_template)
        ):
            raise ValueError("Invalid Zabbix template metrics")
    metrics = data.get("metric_keys")
    if not isinstance(metrics, dict) or "cpu" not in metrics:
        raise ValueError("metric_keys.cpu is required")
    for metric, keys in metrics.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(metric)):
            raise ValueError("Invalid metric name")
        if (
            not isinstance(keys, list)
            or not keys
            or any(not isinstance(key, str) or not KEY_PREFIX.fullmatch(key) for key in keys)
        ):
            raise ValueError(f"Invalid item key mapping for {metric}")
    return data


def resolve_template(
    config: dict,
    platform: str,
    device_type: str = "",
) -> Optional[str]:
    default = config["platform_templates"].get(platform)
    if not default:
        return None
    matches = {
        rule["template"]
        for rule in config.get("device_type_template_rules", {}).get(platform, [])
        if any(
            fnmatchcase(device_type, pattern)
            for pattern in rule["device_type_patterns"]
        )
    }
    if len(matches) > 1:
        raise ValueError("Ambiguous Zabbix device type template mapping")
    return next(iter(matches), default)


def template_metrics(config: dict, template: str) -> tuple[str, ...]:
    return tuple(config["template_metadata"][template]["metrics"])


def required_template_names(config: dict) -> tuple[str, ...]:
    return tuple(sorted(config["template_metadata"]))


__all__ = [
    "CONFIG_FILE",
    "load_zabbix_config",
    "required_template_names",
    "resolve_template",
    "template_metrics",
]
