#!/usr/bin/env python3
"""Create Atlas site runtime configuration from one validated input."""

import argparse
import getpass
import grp
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.deployment import write_private  # noqa: E402
from atlas.auth import ROLE_ADMIN, hash_password, normalize_username  # noqa: E402
from atlas.components import load_components  # noqa: E402
from atlas.inventory.config import normalize_inventory_config  # noqa: E402
from atlas.inventory.importer import slugify  # noqa: E402
from atlas.monitoring.models import load_config as load_monitoring_config  # noqa: E402
from atlas.proxy import HOSTNAME, load_config as load_proxy_config  # noqa: E402
from scripts.import_site_config import import_config as import_proxy_config  # noqa: E402


PRIVATE_NETWORKS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
SERVICES = ("netbox", "oxidized", "zabbix")
ADMIN_BOOTSTRAP = Path(".runtime/auth/bootstrap-admin.json")
ADMIN_MARKER = Path(".runtime/auth/admin-configured")


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping")
    return value


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false")
    return value


def _address(value, label):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(f"{label} must be a valid IP address") from None
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError(f"{label} must be a usable non-loopback address")
    return str(address)


def _ipv4(value, label):
    address = _address(value, label)
    parsed = ipaddress.ip_address(address)
    if parsed.version != 4 or parsed.is_link_local:
        raise ValueError(f"{label} must be an IPv4 address")
    return address


def _networks(value, label, *, ipv4_only=False):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty YAML list")
    result = []
    for entry in value:
        try:
            network = ipaddress.ip_network(entry)
        except ValueError:
            raise ValueError(f"{label} contains an invalid network") from None
        if network.prefixlen == 0:
            raise ValueError(f"{label} must not permit an entire address family")
        if ipv4_only and network.version != 4:
            raise ValueError(f"{label} supports IPv4 networks only")
        result.append(str(network))
    return result


def _resolve_file(value, base, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return str(path)


def _proxy_name(value, label):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise ValueError(f"{label} is invalid")
    return value.strip()


def _egress_profiles(value, allowed_networks):
    profiles = _mapping(value, "monitoring.egress_profiles")
    if not profiles:
        raise ValueError("monitoring.egress_profiles must not be empty")
    normalized = {}
    for name, raw in profiles.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name
        ):
            raise ValueError("monitoring.egress_profiles contains an invalid name")
        raw = _mapping(raw, f"monitoring.egress_profiles.{name}")
        device_networks = _networks(
            raw.get("device_networks"),
            f"monitoring.egress_profiles.{name}.device_networks",
            ipv4_only=True,
        )
        if any(
            not any(
                ipaddress.ip_network(network).subnet_of(
                    ipaddress.ip_network(allowed)
                )
                for allowed in allowed_networks
            )
            for network in device_networks
        ):
            raise ValueError(
                f"monitoring.egress_profiles.{name}.device_networks must be "
                "contained by monitoring.allowed_device_networks"
            )
        profile = {
            "device_networks": device_networks,
            "source_ip": _ipv4(
                raw.get("source_ip"),
                f"monitoring.egress_profiles.{name}.source_ip",
            ),
        }
        if "proxy_name" in raw:
            profile["proxy_name"] = _proxy_name(
                raw["proxy_name"],
                f"monitoring.egress_profiles.{name}.proxy_name",
            )
        normalized[name] = profile
    # The canonical monitoring loader enforces overlap, allowed-network and
    # one-source-per-poller constraints on the rendered document.
    return normalized


def normalize_site(raw, base, expected_services=SERVICES):
    raw = _mapping(raw, "Site configuration")
    if raw.get("version") != 1:
        raise ValueError("Site configuration version must be 1")

    proxy = _mapping(raw.get("proxy"), "proxy")
    normalized_proxy = {
        "listen_address": _address(proxy.get("listen_address"), "proxy.listen_address"),
        "https_port": _integer(proxy.get("https_port", 443), "proxy.https_port", 1, 65535),
        "allowed_networks": _networks(proxy.get("allowed_networks"), "proxy.allowed_networks"),
    }

    services = _mapping(raw.get("services"), "services")
    expected_services = tuple(expected_services)
    if set(services) != set(expected_services):
        raise ValueError(
            "services must contain exactly the selected ingress services: "
            + ", ".join(expected_services)
        )
    normalized_services = {}
    hostnames = []
    for name in expected_services:
        service = _mapping(services[name], f"services.{name}")
        hostname = service.get("hostname")
        if not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname):
            raise ValueError(f"services.{name}.hostname is invalid")
        normalized_services[name] = {"hostname": hostname}
        hostnames.append(hostname)
    if len(set(hostnames)) != len(hostnames):
        raise ValueError("Every service must use a unique DNS hostname")

    tls = _mapping(raw.get("tls"), "tls")
    mode = tls.get("mode", "existing")
    if mode not in {"existing", "lab_generated"}:
        raise ValueError("tls.mode must be existing or lab_generated")
    normalized_tls = {"mode": mode}
    if mode == "existing":
        normalized_tls["ca_certificate"] = _resolve_file(
            tls.get("ca_certificate"), base, "tls.ca_certificate"
        )
        for name in expected_services:
            service = services[name]
            normalized_services[name].update(
                {
                    "certificate": _resolve_file(
                        service.get("certificate"),
                        base,
                        f"services.{name}.certificate",
                    ),
                    "private_key": _resolve_file(
                        service.get("private_key"),
                        base,
                        f"services.{name}.private_key",
                    ),
                }
            )
    else:
        normalized_tls["validity_days"] = _integer(
            tls.get("validity_days", 30), "tls.validity_days", 1, 90
        )

    monitoring = _mapping(raw.get("monitoring"), "monitoring")
    allowed_device_networks = _networks(
        monitoring.get("allowed_device_networks", PRIVATE_NETWORKS),
        "monitoring.allowed_device_networks",
        ipv4_only=True,
    )
    normalized_monitoring = {
        "allowed_device_networks": allowed_device_networks,
        "default_snmp_port": _integer(
            monitoring.get("default_snmp_port", 161),
            "monitoring.default_snmp_port",
            1,
            65535,
        ),
        "aruba_aoscx_vrf": monitoring.get("aruba_aoscx_vrf", "default"),
        "allow_unrestricted_snmp_v2c": _boolean(
            monitoring.get("allow_unrestricted_snmp_v2c", False),
            "monitoring.allow_unrestricted_snmp_v2c",
        ),
    }
    source_ip = monitoring.get("source_ip")
    profiles = monitoring.get("egress_profiles")
    if source_ip is not None and profiles is not None:
        raise ValueError("monitoring.source_ip and egress_profiles are mutually exclusive")
    if profiles is not None:
        if "proxy_name" in monitoring:
            raise ValueError("monitoring.proxy_name belongs inside each egress profile")
        normalized_monitoring["egress_profiles"] = _egress_profiles(
            profiles, allowed_device_networks
        )
    else:
        normalized_monitoring["source_ip"] = _ipv4(
            source_ip, "monitoring.source_ip"
        )
    if not isinstance(normalized_monitoring["aruba_aoscx_vrf"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,32}", normalized_monitoring["aruba_aoscx_vrf"]
    ):
        raise ValueError("monitoring.aruba_aoscx_vrf is invalid")
    if "proxy_name" in monitoring:
        normalized_monitoring["proxy_name"] = _proxy_name(
            monitoring["proxy_name"], "monitoring.proxy_name"
        )

    fortios = _mapping(raw.get("fortios", {}), "fortios")
    normalized_fortios = {
        "default_vdom": fortios.get("default_vdom", "root"),
        "default_https_port": _integer(
            fortios.get("default_https_port", 443),
            "fortios.default_https_port",
            1,
            65535,
        ),
        "default_snmp_port": _integer(
            fortios.get("default_snmp_port", 161),
            "fortios.default_snmp_port",
            1,
            65535,
        ),
        "tls_verify": _boolean(fortios.get("tls_verify", True), "fortios.tls_verify"),
        "lab_tls_exception": _boolean(
            fortios.get("lab_tls_exception", False),
            "fortios.lab_tls_exception",
        ),
    }
    if not isinstance(normalized_fortios["default_vdom"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,35}", normalized_fortios["default_vdom"]
    ):
        raise ValueError("fortios.default_vdom is invalid")
    if normalized_fortios["tls_verify"] is not True and not (
        normalized_fortios["tls_verify"] is False
        and normalized_fortios["lab_tls_exception"] is True
    ):
        raise ValueError("Disabling FortiOS TLS requires lab_tls_exception: true")
    if "ca_bundle" in fortios:
        if not normalized_fortios["tls_verify"]:
            raise ValueError("fortios.ca_bundle requires tls_verify: true")
        normalized_fortios["ca_bundle"] = _resolve_file(
            fortios["ca_bundle"], base, "fortios.ca_bundle"
        )

    return {
        "version": 1,
        "proxy": normalized_proxy,
        "tls": normalized_tls,
        "services": normalized_services,
        "monitoring": normalized_monitoring,
        "fortios": normalized_fortios,
        "inventory": normalize_inventory_config(raw.get("inventory", {})),
    }


def generate_lab_tls(site, directory):
    directory.mkdir(mode=0o700)
    ca_key = directory / "ca.key"
    ca_cert = directory / "ca.crt"
    server_key = directory / "server.key"
    request = directory / "server.csr"
    extension = directory / "server.ext"
    server_cert = directory / "server.crt"
    names = [service["hostname"] for service in site["services"].values()]
    extension.write_text(
        "subjectAltName=" + ",".join(f"DNS:{name}" for name in names) + "\n"
        "extendedKeyUsage=serverAuth\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="utf-8",
    )
    commands = (
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-days", str(site["tls"]["validity_days"]), "-subj", "/CN=Atlas Lab CA",
            "-keyout", str(ca_key), "-out", str(ca_cert),
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
        ],
        [
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-subj", f"/CN={names[0]}", "-keyout", str(server_key), "-out", str(request),
        ],
        [
            "openssl", "x509", "-req", "-sha256", "-days",
            str(site["tls"]["validity_days"]), "-in", str(request), "-CA", str(ca_cert),
            "-CAkey", str(ca_key), "-CAcreateserial", "-extfile", str(extension),
            "-out", str(server_cert),
        ],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"{command[0]} failed while generating LAB TLS material")
    site["tls"]["ca_certificate"] = str(ca_cert)
    for name in site["services"]:
        site["services"][name]["certificate"] = str(server_cert)
        site["services"][name]["private_key"] = str(server_key)


def proxy_document(site):
    template = yaml.safe_load(
        (ROOT / "deployment/proxy.example.yaml").read_text(encoding="utf-8")
    )
    template["proxy"].update(site["proxy"])
    template["tls"]["ca_certificate"] = site["tls"]["ca_certificate"]
    template["services"] = {
        name: {
            **template["services"][name],
            **service,
        }
        for name, service in site["services"].items()
    }
    return template


def monitoring_document(site, *, installed=False):
    fortios = dict(site["fortios"])
    if "ca_bundle" in fortios and installed:
        fortios["ca_bundle"] = "/run/atlas/monitoring/trust/fortigate-ca.pem"
    monitoring = {
        key: value
        for key, value in site["monitoring"].items()
        if key != "allowed_device_networks"
    }
    return {
        "version": 2,
        "allowed_device_networks": site["monitoring"]["allowed_device_networks"],
        "fortios": fortios,
        "monitoring": monitoring,
    }


def validate_documents(site, directory):
    proxy_path = directory / "proxy.yaml"
    proxy_path.write_text(
        yaml.safe_dump(proxy_document(site), sort_keys=False), encoding="utf-8"
    )
    load_proxy_config(proxy_path)
    monitoring_path = directory / "monitoring.yaml"
    validation_document = monitoring_document(site)
    monitoring_path.write_text(
        yaml.safe_dump(validation_document, sort_keys=False), encoding="utf-8"
    )
    load_monitoring_config(monitoring_path)


def _managed_paths(target_root, site):
    runtime = target_root / ".runtime"
    paths = [
        runtime / "proxy.yaml",
        runtime / "inventory.yaml",
        runtime / "monitoring/config.yaml",
        runtime / "tls/ca.crt",
    ]
    for name in site["services"]:
        paths.extend([runtime / f"tls/{name}.crt", runtime / f"tls/{name}.key"])
    if "ca_bundle" in site["fortios"]:
        paths.append(runtime / "monitoring/trust/fortigate-ca.pem")
    return paths


def _ensure_safe_targets(target_root, site, force):
    target_root = target_root.resolve()
    runtime = target_root / ".runtime"
    for directory in (runtime, runtime / "tls", runtime / "monitoring", runtime / "monitoring/trust"):
        if directory.is_symlink():
            raise RuntimeError(f"Managed directory must not be a symlink: {directory}")
    existing = [path for path in _managed_paths(target_root, site) if path.exists() or path.is_symlink()]
    if existing and not force:
        rendered = "\n  ".join(str(path) for path in existing)
        raise RuntimeError(
            "Site configuration already exists and was not changed. "
            "Review it and rerun with --force to replace managed files:\n  " + rendered
        )
    if any(path.is_symlink() for path in existing):
        raise RuntimeError("Managed configuration targets must not be symlinks")


def install_site(site, target_root, owner, group, force=False):
    target_root = target_root.resolve()
    _ensure_safe_targets(target_root, site, force)
    uid = pwd.getpwnam(owner).pw_uid
    gid = grp.getgrnam(group).gr_gid
    runtime = target_root / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    os.chown(runtime, uid, gid)

    with tempfile.TemporaryDirectory(prefix="atlas-site-") as temporary:
        temporary_path = Path(temporary)
        if site["tls"]["mode"] == "lab_generated":
            generate_lab_tls(site, temporary_path / "lab-tls")
        validate_documents(site, temporary_path)
        source_proxy = temporary_path / "proxy.yaml"
        source_proxy.write_text(
            yaml.safe_dump(proxy_document(site), sort_keys=False), encoding="utf-8"
        )
        proxy_target = import_proxy_config(source_proxy, target_root, owner, group)

        monitoring_directory = runtime / "monitoring"
        trust_directory = monitoring_directory / "trust"
        monitoring_directory.mkdir(mode=0o700, exist_ok=True)
        trust_directory.mkdir(mode=0o700, exist_ok=True)
        for directory in (monitoring_directory, trust_directory):
            os.chmod(directory, 0o700)
            os.chown(directory, uid, gid)
        if "ca_bundle" in site["fortios"]:
            target = trust_directory / "fortigate-ca.pem"
            target.write_bytes(Path(site["fortios"]["ca_bundle"]).read_bytes())
            os.chmod(target, 0o600)
            os.chown(target, uid, gid)
        monitoring_target = monitoring_directory / "config.yaml"
        write_private(
            monitoring_target,
            yaml.safe_dump(monitoring_document(site, installed=True), sort_keys=False),
            mode=0o600,
            group_id=gid,
        )
        os.chown(monitoring_target, uid, gid)

        inventory_target = runtime / "inventory.yaml"
        write_private(
            inventory_target,
            yaml.safe_dump(site["inventory"], sort_keys=False),
            mode=0o600,
            group_id=gid,
        )
        os.chown(inventory_target, uid, gid)

    return proxy_target, monitoring_target


def _prompt(label, default=None):
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _collect_admin_bootstrap(target_root):
    bootstrap = target_root / ADMIN_BOOTSTRAP
    marker = target_root / ADMIN_MARKER
    if marker.is_file() or bootstrap.is_file():
        return None
    print("\nAtlas local administrator")
    print("This account protects the Operations UI and privileged atlasctl commands.")
    username = normalize_username(_prompt("Administrator username", "admin"))
    password = getpass.getpass("Administrator password (minimum 14 characters): ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        raise ValueError("Administrator passwords do not match")
    return {
        "schema_version": 1,
        "username": username,
        "role": ROLE_ADMIN,
        "active": True,
        "password_hash": hash_password(password),
    }


def _write_admin_bootstrap(target_root, owner, group, record):
    if record is None:
        return None
    uid = pwd.getpwnam(owner).pw_uid
    gid = grp.getgrnam(group).gr_gid
    directory = target_root / ADMIN_BOOTSTRAP.parent
    target = target_root / ADMIN_BOOTSTRAP
    if directory.is_symlink() or target.is_symlink():
        raise RuntimeError("Administrator bootstrap path must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    os.chown(directory, uid, gid)
    write_private(
        target,
        json.dumps(record, sort_keys=True) + "\n",
        mode=0o600,
        group_id=gid,
    )
    os.chown(target, uid, gid)
    return target


def interactive_site(service_names=SERVICES):
    print("Atlas first-boot site configuration")
    print("Values are validated before runtime files are written.\n")
    listen = _prompt("Atlas management/listen IP")
    domain = _prompt("DNS suffix, for example atlas.example.internal")
    tls_mode = _prompt("TLS mode (existing or lab_generated)", "existing")
    tenant_name = _prompt("Customer / Tenant name (optional)", "")
    inventory = {}
    if tenant_name:
        default_slug = slugify(tenant_name)
        tenant_slug = _prompt("Tenant slug", default_slug)
        inventory["default_tenant"] = {
            "name": tenant_name,
            "slug": tenant_slug,
            "provision": True,
        }
        print(
            "[INFO] Atlas will reuse this NetBox Tenant when it exists or create "
            "it during deployment when it is missing."
        )
        site_name = _prompt("Default inventory Site name (optional)", "")
        if site_name:
            inventory["default_site"] = {
                "name": site_name,
                "slug": slugify(site_name),
                "provision": True,
            }
    monitoring_source = (
        _prompt("Zabbix SNMP source IP", listen)
        if "zabbix" in service_names
        else listen
    )
    site = {
        "version": 1,
        "proxy": {
            "listen_address": listen,
            "https_port": int(_prompt("HTTPS port", "443")),
            "allowed_networks": [
                value.strip()
                for value in _prompt(
                    "Administrator CIDRs (comma separated)", f"{listen}/32"
                ).split(",")
                if value.strip()
            ],
        },
        "tls": {"mode": tls_mode},
        "services": {
            name: {"hostname": f"{name}.{domain}"} for name in service_names
        },
        "monitoring": {
            "source_ip": monitoring_source,
            "allowed_device_networks": PRIVATE_NETWORKS,
        },
        "inventory": inventory,
    }
    if tls_mode == "existing":
        site["tls"]["ca_certificate"] = _prompt("CA bundle path")
        for name in service_names:
            site["services"][name]["certificate"] = _prompt(
                f"{name} certificate/full-chain path"
            )
            site["services"][name]["private_key"] = _prompt(
                f"{name} private-key path"
            )
    elif tls_mode == "lab_generated":
        print("WARNING: LAB TLS is temporary and is not suitable for production.")
        site["tls"]["validity_days"] = int(_prompt("LAB certificate validity days", "30"))
    return site


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-file", type=Path)
    parser.add_argument("--target-root", type=Path, default=ROOT)
    parser.add_argument("--owner", default="atlas")
    parser.add_argument("--group", default="atlas")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--admin-only", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if os.geteuid() != 0 and not args.check:
        raise RuntimeError("Run configuration writes as root or use --check")
    if args.admin_only:
        if args.check or args.site_file or args.force:
            raise ValueError("--admin-only cannot be combined with other configuration modes")
        record = _collect_admin_bootstrap(args.target_root)
        target = _write_admin_bootstrap(
            args.target_root, args.owner, args.group, record
        )
        if target is None:
            print("[OK] Atlas administrator bootstrap is already configured")
        else:
            print(f"[OK] Prepared one-time administrator bootstrap: {target}")
            print(
                "[INFO] On an existing OpenBao instance, first run 'sudo atlasctl "
                "openbao' and reconcile Atlas backend roles, then deploy"
            )
        return
    selection = load_components(args.target_root / ".runtime/components.yaml")
    service_names = selection.ingress_services
    if args.site_file:
        path = args.site_file.expanduser().resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        base = path.parent
    else:
        raw = interactive_site(service_names)
        base = Path.cwd()
    site = normalize_site(raw, base, service_names)
    with tempfile.TemporaryDirectory(prefix="atlas-site-check-") as temporary:
        if site["tls"]["mode"] == "lab_generated":
            generate_lab_tls(site, Path(temporary) / "lab-tls")
        validate_documents(site, Path(temporary))
    print("[OK] Site configuration and TLS material validated")
    print(f"[INFO] Proxy listen address: {site['proxy']['listen_address']}:{site['proxy']['https_port']}")
    if site["monitoring"].get("egress_profiles"):
        summaries = []
        for name, profile in site["monitoring"]["egress_profiles"].items():
            poller = profile.get("proxy_name", "direct-server")
            summaries.append(f"{name}={profile['source_ip']} via {poller}")
        print("[INFO] Monitoring egress profiles: " + "; ".join(summaries))
    else:
        print(f"[INFO] Monitoring source IP: {site['monitoring']['source_ip']}")
    if args.check:
        print("[OK] Check completed; no files were written")
        return
    admin_bootstrap = _collect_admin_bootstrap(args.target_root)
    proxy_target, monitoring_target = install_site(
        site, args.target_root, args.owner, args.group, force=args.force
    )
    bootstrap_target = _write_admin_bootstrap(
        args.target_root, args.owner, args.group, admin_bootstrap
    )
    print(f"[OK] Wrote protected proxy configuration: {proxy_target}")
    print(f"[OK] Wrote protected monitoring configuration: {monitoring_target}")
    if bootstrap_target:
        print(f"[OK] Prepared one-time administrator bootstrap: {bootstrap_target}")
    if site["tls"]["mode"] == "lab_generated":
        print("[WARN] LAB TLS was generated; distribute the installed ca.crt only to lab clients")
    print("[OK] Site configuration is ready; run sudo atlasctl deploy")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"[ERROR] {exc}")
