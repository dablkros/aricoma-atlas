"""Validated, centralized Zabbix platform and metric mappings."""

import re
from functools import lru_cache
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "catalog/zabbix.yaml"
NAME = re.compile(r"[^\x00-\x1f]{1,128}")
KEY_PREFIX = re.compile(r"[A-Za-z0-9_.]+")


@lru_cache(maxsize=1)
def load_zabbix_config(path: Path = CONFIG_FILE) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read Zabbix mapping: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Zabbix mapping version must be 1")
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


__all__ = ["CONFIG_FILE", "load_zabbix_config"]
