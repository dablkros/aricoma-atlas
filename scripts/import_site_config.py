#!/usr/bin/env python3
"""Import site-specific proxy/TLS inputs into the installed Atlas runtime."""

import argparse
import grp
import os
import pwd
import shutil
from pathlib import Path

import yaml


def require_file(path, label):
    if not path.is_file():
        raise RuntimeError(f"{label} not found: {path}")
    return path


def resolve_input(value, source_config):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (source_config.parent / path).resolve()
    return path


def copy_owned(source, target, mode, uid, gid):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, mode)
    os.chown(target, uid, gid)


def import_config(source_config, target_root, owner, group):
    source_config = source_config.resolve()
    target_root = target_root.resolve()

    raw = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("Proxy configuration must have a YAML mapping root")

    uid = pwd.getpwnam(owner).pw_uid
    gid = grp.getgrnam(group).gr_gid

    runtime = target_root / ".runtime"
    tls_dir = runtime / "tls"
    runtime.mkdir(parents=True, exist_ok=True)
    tls_dir.mkdir(parents=True, exist_ok=True)
    os.chown(runtime, uid, gid)
    os.chown(tls_dir, uid, gid)
    os.chmod(runtime, 0o700)
    os.chmod(tls_dir, 0o700)

    tls = raw.get("tls")
    if not isinstance(tls, dict) or not tls.get("ca_certificate"):
        raise RuntimeError("Proxy configuration is missing tls.ca_certificate")

    ca_source = require_file(
        resolve_input(tls["ca_certificate"], source_config),
        "CA certificate",
    )
    ca_target = tls_dir / "ca.crt"
    copy_owned(ca_source, ca_target, 0o644, uid, gid)
    tls["ca_certificate"] = str(ca_target)

    services = raw.get("services")
    if not isinstance(services, dict) or not services:
        raise RuntimeError("Proxy configuration is missing services")

    for name, service in services.items():
        if not isinstance(service, dict):
            raise RuntimeError(f"Invalid service configuration: {name}")

        certificate = service.get("certificate")
        private_key = service.get("private_key")
        if not certificate or not private_key:
            raise RuntimeError(
                f"Service {name} must define certificate and private_key"
            )

        cert_source = require_file(
            resolve_input(certificate, source_config),
            f"Certificate for {name}",
        )
        key_source = require_file(
            resolve_input(private_key, source_config),
            f"Private key for {name}",
        )

        cert_target = tls_dir / f"{name}.crt"
        key_target = tls_dir / f"{name}.key"
        copy_owned(cert_source, cert_target, 0o644, uid, gid)
        copy_owned(key_source, key_target, 0o600, uid, gid)

        service["certificate"] = str(cert_target)
        service["private_key"] = str(key_target)

    target_config = runtime / "proxy.yaml"
    target_config.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(target_config, 0o600)
    os.chown(target_config, uid, gid)

    return target_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--owner", default="atlas")
    parser.add_argument("--group", default="atlas")
    args = parser.parse_args()

    target = import_config(
        args.source,
        args.target_root,
        args.owner,
        args.group,
    )
    print(f"[OK] Imported Atlas site configuration: {target}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
