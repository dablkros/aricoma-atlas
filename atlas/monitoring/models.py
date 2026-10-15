"""Non-secret configuration and validated NetBox device references."""
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


def device_reference(raw):
    fields = raw.get("custom_fields") or {}
    if fields.get("monitoring_enabled") is not True:
        raise MonitoringError("monitoring_disabled")
    if (raw.get("status") or {}).get("value") != "active":
        raise MonitoringError("device_not_active")
    vendor = ((raw.get("device_type") or {}).get("manufacturer") or {}).get("slug")
    platform = (raw.get("platform") or {}).get("slug")
    if (vendor, platform) != ("fortinet", "fortios"):
        raise MonitoringError("unsupported_monitoring_driver")
    profile = fields.get("credential_profile")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile):
        raise MonitoringError("missing_or_invalid_credential_profile")
    primary = raw.get("primary_ip4") or raw.get("primary_ip6") or raw.get("primary_ip") or {}
    try:
        address = ip_interface(primary.get("address")).ip
        if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError()
    except (ValueError, TypeError):
        raise MonitoringError("invalid_management_ip") from None
    if type(raw.get("id")) is not int or raw["id"] < 1:
        raise MonitoringError("invalid_device_id")
    if not isinstance(raw.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", raw["name"]):
        raise MonitoringError("invalid_device_name")
    site = (raw.get("site") or {}).get("slug")
    if not isinstance(site, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", site):
        raise MonitoringError("missing_or_invalid_site")
    return Device(raw["id"], raw["name"], str(address), vendor, platform, profile, site)


def load_config(path):
    try:
        config = yaml.safe_load(Path(path).read_text())
        if not isinstance(config, dict) or config.get("version") != 1:
            raise ValueError()
        instance = config["netbox_instance"]
        if not isinstance(instance, str) or not re.fullmatch(r"[a-z0-9-]{1,48}", instance):
            raise ValueError()
        networks = [ip_network(n) for n in config["allowed_device_networks"]]
        if not networks or any(n.version != 4 or n.prefixlen == 0 for n in networks):
            raise ValueError()
        if not isinstance(config.get("devices"), dict):
            raise ValueError()
        return config
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise MonitoringError("monitoring_configuration_required", 503) from None


def device_options(device, config):
    # Operator-controlled file, not arbitrary request URLs/paths.
    options = dict(config["devices"].get(str(device.id), config["devices"].get(device.id, {})))
    if not any(ip_address(device.address) in ip_network(n) for n in config["allowed_device_networks"]):
        raise MonitoringError("management_ip_not_allowed")
    for key in ("https_port", "snmp_port"):
        value = options.get(key, 443 if key == "https_port" else 161)
        if type(value) is not int or not 1 <= value <= 65535:
            raise MonitoringError("invalid_monitoring_port")
        options[key] = value
    for key in ("interface", "vdom"):
        if not isinstance(options.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,35}", options[key]):
            raise MonitoringError("management_interface_and_vdom_required")
    sources = options.get("source_ips", [])
    try:
        if not isinstance(sources, list) or not sources:
            raise ValueError()
        for value in sources:
            ip = ip_address(value)
            if ip.version != 4 or ip.is_unspecified or ip.is_multicast or ip.is_loopback:
                raise ValueError()
    except (ValueError, TypeError):
        raise MonitoringError("explicit_monitoring_source_required") from None
    options["source_ips"] = sorted(set(sources))
    options.setdefault("tls_verify", True)
    if options["tls_verify"] is not True and not (
        options["tls_verify"] is False and options.get("lab_tls_exception") is True
    ):
        raise MonitoringError("invalid_tls_configuration")
    if options.get("ca_bundle"):
        if not options["tls_verify"] or not Path(options["ca_bundle"]).is_file():
            raise MonitoringError("invalid_ca_bundle")
    if options.get("proxy_name") is not None and not isinstance(options["proxy_name"], str):
        raise MonitoringError("invalid_proxy_name")
    return options
