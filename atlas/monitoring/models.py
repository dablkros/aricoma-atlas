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
    device_type: str = ""


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
    device_type = raw.get("device_type") or {}
    vendor = (device_type.get("manufacturer") or {}).get("slug")
    device_type_slug = device_type.get("slug")
    if device_type_slug is not None and (
        not isinstance(device_type_slug, str)
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", device_type_slug)
    ):
        raise MonitoringError("invalid_device_type")
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
    return Device(
        raw["id"],
        raw["name"],
        str(address),
        vendor,
        platform,
        profile,
        site,
        device_type_slug or "",
    )


def _port(value, default):
    value = default if value is None else value
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError()
    return value


def _source_ip(value):
    parsed = ip_address(value)
    if (
        parsed.version != 4
        or parsed.is_unspecified
        or parsed.is_multicast
        or parsed.is_loopback
        or parsed.is_link_local
    ):
        raise ValueError()
    return str(parsed)


def _proxy_name(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 128
    ):
        raise ValueError()
    return value.strip()


def _egress_profiles(value, allowed_networks):
    if not isinstance(value, dict) or not value:
        raise ValueError()
    profiles = {}
    claimed = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name
        ):
            raise ValueError()
        if not isinstance(raw, dict):
            raise ValueError()
        networks = [ip_network(entry) for entry in raw["device_networks"]]
        if (
            not networks
            or any(network.version != 4 or network.prefixlen == 0 for network in networks)
            or any(
                not any(network.subnet_of(allowed) for allowed in allowed_networks)
                for network in networks
            )
        ):
            raise ValueError()
        for network in networks:
            rendered = str(network)
            if rendered in claimed and claimed[rendered] != name:
                raise MonitoringError("monitoring_egress_profile_ambiguous", 503)
            claimed[rendered] = name
        profile = {
            "device_networks": [str(network) for network in networks],
            "source_ip": _source_ip(raw["source_ip"]),
        }
        if raw.get("proxy_name") is not None:
            profile["proxy_name"] = _proxy_name(raw["proxy_name"])
        profiles[name] = profile

    return profiles


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
        profiles = monitoring.get("egress_profiles")
        if source is not None and profiles is not None:
            raise MonitoringError("monitoring_egress_configuration_conflict", 503)
        if profiles is not None:
            if monitoring.get("proxy_name") is not None:
                raise MonitoringError("monitoring_egress_configuration_conflict", 503)
            monitoring = {
                **monitoring,
                "egress_profiles": _egress_profiles(profiles, networks),
            }
        elif source is not None:
            monitoring = {**monitoring, "source_ip": _source_ip(source)}
        proxy_name = monitoring.get("proxy_name")
        if proxy_name is not None:
            monitoring = {**monitoring, "proxy_name": _proxy_name(proxy_name)}
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


def resolve_egress(device, monitoring):
    profiles = monitoring.get("egress_profiles")
    if not profiles:
        source = monitoring.get("source_ip")
        if not source:
            raise MonitoringError("monitoring_source_ip_unresolved")
        return {
            "source_ip": source,
            **(
                {"proxy_name": monitoring["proxy_name"]}
                if monitoring.get("proxy_name")
                else {}
            ),
        }

    target = ip_address(device.address)
    matches = []
    for name, profile in profiles.items():
        for value in profile["device_networks"]:
            network = ip_network(value)
            if target in network:
                matches.append((network.prefixlen, name, network, profile))
    if not matches:
        raise MonitoringError("monitoring_egress_profile_unresolved")
    longest = max(match[0] for match in matches)
    selected = [match for match in matches if match[0] == longest]
    names = {match[1] for match in selected}
    if len(names) != 1:
        raise MonitoringError("monitoring_egress_profile_ambiguous", 503)
    _, name, network, profile = selected[0]
    return {
        "source_ip": profile["source_ip"],
        "egress_profile": name,
        "egress_network": str(network),
        **(
            {"proxy_name": profile["proxy_name"]}
            if profile.get("proxy_name")
            else {}
        ),
    }


def site_options(device, config, *, https_port=None, interface=None, ssh_port=22):
    ensure_device_network_allowed(device, config)
    egress = resolve_egress(device, config["monitoring"])
    snmp_port = (
        config["fortios"]["default_snmp_port"]
        if device.platform == "fortios"
        else config["monitoring"].get("default_snmp_port", 161)
    )
    if device.platform != "fortios" and snmp_port != 161:
        raise MonitoringError("unsupported_snmp_port")
    options = {
        "snmp_port": snmp_port,
        "source_ips": [egress["source_ip"]],
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
    for key in ("egress_profile", "egress_network", "proxy_name"):
        if key in egress:
            options[key] = egress[key]
    return options


__all__ = [
    "Device",
    "MonitoringError",
    "SUPPORTED_MONITORING_DEVICES",
    "device_reference",
    "ensure_device_network_allowed",
    "load_config",
    "resolve_egress",
    "site_options",
]
