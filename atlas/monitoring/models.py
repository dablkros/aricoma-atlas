"""Monitoring policy and validated NetBox device references."""
from dataclasses import dataclass
from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path
import re

import yaml


class MonitoringError(RuntimeError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class Device:
    id: int
    name: str
    address: str
    vendor: str
    platform: str
    profile: str
    site: str


SUPPORTED_MONITORING_DEVICES = frozenset(
    {
        ("fortinet", "fortios"),
        ("cisco", "cisco-ios"),
        ("cisco", "cisco-ios-xe"),
        ("cisco", "cisco-cbs"),
        ("cisco", "cisco-cbs250-350"),
        ("hpe", "aruba-aos-cx"),
        ("hpe", "aruba-aos-switch"),
    }
)


def device_reference(raw):
    fields = raw.get("custom_fields") or {}
    if fields.get("monitoring_enabled") is not True:
        raise MonitoringError("monitoring_disabled")
    status = raw.get("status") or {}
    if (status.get("value") if isinstance(status, dict) else status) != "active":
        raise MonitoringError("device_not_active")
    vendor = ((raw.get("device_type") or {}).get("manufacturer") or {}).get("slug")
    platform = (raw.get("platform") or {}).get("slug")
    if (vendor, platform) not in SUPPORTED_MONITORING_DEVICES:
        raise MonitoringError("unsupported_monitoring_driver")
    profile = fields.get("credential_profile")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile):
        raise MonitoringError("missing_credential_profile")
    primary = raw.get("primary_ip4") or {}
    try:
        address = ip_interface(primary.get("address")).ip
        if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError()
    except (ValueError, TypeError):
        raise MonitoringError("missing_primary_ip") from None
    if type(raw.get("id")) is not int or raw["id"] < 1:
        raise MonitoringError("invalid_device_id")
    if not isinstance(raw.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", raw["name"]):
        raise MonitoringError("invalid_device_name")
    site = (raw.get("site") or {}).get("slug")
    if not isinstance(site, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", site):
        raise MonitoringError("missing_or_invalid_site")
    return Device(raw["id"], raw["name"], str(address), vendor, platform, profile, site)


def _port(value, default):
    value = default if value is None else value
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError()
    return value


def load_config(path):
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError()
        if config.get("version") == 1 or "devices" in config or "netbox_instance" in config:
            raise MonitoringError("monitoring_configuration_v1_migration_required", 503)
        if config.get("version") != 2:
            raise ValueError()
        networks = [ip_network(value) for value in config["allowed_device_networks"]]
        if not networks or any(network.version != 4 or network.prefixlen == 0 for network in networks):
            raise ValueError()
        fortios = config.get("fortios") or {}
        if not isinstance(fortios, dict):
            raise ValueError()
        default_vdom = fortios.get("default_vdom", "root")
        if not isinstance(default_vdom, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,35}", default_vdom):
            raise ValueError()
        normalized = dict(config)
        normalized["allowed_device_networks"] = [str(network) for network in networks]
        normalized["fortios"] = {
            **fortios,
            "default_vdom": default_vdom,
            "default_https_port": _port(fortios.get("default_https_port"), 443),
            "default_snmp_port": _port(fortios.get("default_snmp_port"), 161),
            "tls_verify": fortios.get("tls_verify", True),
            "lab_tls_exception": fortios.get("lab_tls_exception", False),
        }
        if normalized["fortios"]["tls_verify"] is not True and not (
            normalized["fortios"]["tls_verify"] is False
            and normalized["fortios"]["lab_tls_exception"] is True
        ):
            raise ValueError()
        ca_bundle = normalized["fortios"].get("ca_bundle")
        if ca_bundle and (not normalized["fortios"]["tls_verify"] or not Path(ca_bundle).is_file()):
            raise ValueError()
        monitoring = config.get("monitoring") or {}
        if not isinstance(monitoring, dict):
            raise ValueError()
        source = monitoring.get("source_ip")
        if source is not None:
            parsed = ip_address(source)
            if parsed.version != 4 or parsed.is_unspecified or parsed.is_multicast or parsed.is_loopback:
                raise ValueError()
            monitoring = {**monitoring, "source_ip": str(parsed)}
        proxy_name = monitoring.get("proxy_name")
        if proxy_name is not None and (
            not isinstance(proxy_name, str) or not proxy_name.strip() or len(proxy_name) > 128
        ):
            raise ValueError()
        allow_unrestricted = monitoring.get("allow_unrestricted_snmp_v2c", False)
        if type(allow_unrestricted) is not bool:
            raise ValueError()
        vrf = monitoring.get("aruba_aoscx_vrf", "default")
        if not isinstance(vrf, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", vrf):
            raise ValueError()
        monitoring = {
            **monitoring,
            "default_snmp_port": _port(
                monitoring.get("default_snmp_port"),
                normalized["fortios"]["default_snmp_port"],
            ),
            "allow_unrestricted_snmp_v2c": allow_unrestricted,
            "aruba_aoscx_vrf": vrf,
        }
        normalized["monitoring"] = monitoring
        return normalized
    except MonitoringError:
        raise
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise MonitoringError("monitoring_configuration_required", 503) from None


def ensure_device_network_allowed(device, config):
    if not any(ip_address(device.address) in ip_network(value) for value in config["allowed_device_networks"]):
        raise MonitoringError("device_network_not_allowed")


def site_options(device, config, *, https_port=None, interface=None, ssh_port=22):
    ensure_device_network_allowed(device, config)
    source = config["monitoring"].get("source_ip")
    if not source:
        raise MonitoringError("monitoring_source_ip_unresolved")
    snmp_port = (
        config["fortios"]["default_snmp_port"]
        if device.platform == "fortios"
        else config["monitoring"].get("default_snmp_port", 161)
    )
    if device.platform != "fortios" and snmp_port != 161:
        raise MonitoringError("unsupported_snmp_port")
    options = {
        "snmp_port": snmp_port,
        "source_ips": [source],
        "allow_unrestricted_snmp_v2c": config["monitoring"].get(
            "allow_unrestricted_snmp_v2c", False
        ),
        "aruba_aoscx_vrf": config["monitoring"].get(
            "aruba_aoscx_vrf", "default"
        ),
    }
    if device.platform == "fortios":
        if https_port is None or not interface:
            raise MonitoringError("management_interface_unresolved")
        fortios = config["fortios"]
        options.update(
            {
                "https_port": https_port,
                "default_vdom": fortios["default_vdom"],
                "interface": interface,
                "tls_verify": fortios["tls_verify"],
            }
        )
        for key in ("lab_tls_exception", "ca_bundle"):
            if key in fortios:
                options[key] = fortios[key]
    else:
        options["ssh_port"] = _port(ssh_port, 22)
    if config["monitoring"].get("proxy_name"):
        options["proxy_name"] = config["monitoring"]["proxy_name"].strip()
    return options


__all__ = [
    "Device",
    "MonitoringError",
    "SUPPORTED_MONITORING_DEVICES",
    "device_reference",
    "ensure_device_network_allowed",
    "load_config",
    "site_options",
]
