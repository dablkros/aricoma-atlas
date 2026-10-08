#!/usr/bin/env python3
"""Deploy shared Nginx HTTPS ingress using customer-provided CA certificates."""

import argparse
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    NETBOX_NETWORK, OXIDIZED_NETWORK, ZABBIX_NETWORK, connect_openbao, ensure_network,
    wait_healthy,
)
from atlas.proxy import ensure_web_secret, load_config, prepare_runtime  # noqa: E402


def verify_listen_address(address):
    """Fail clearly when site configuration contains a stale host address."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
    except OSError as exc:
        raise RuntimeError(
            "Nginx proxy.listen_address is not assigned to this host: "
            f"{address}. Update .runtime/proxy.yaml after an IP address change."
        ) from exc


def compose_step(file, label, *args):
    """Run one non-secret proxy Compose step with actionable diagnostics."""
    result = subprocess.run(
        ["docker", "compose", "-p", "atlas-proxy", "-f", str(file), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return

    # The generated proxy Compose file contains paths and a password hash, but
    # no plaintext credential. Keep output bounded while preserving Docker's
    # actual bind, network, image, or Nginx validation error.
    detail = (result.stderr or result.stdout).strip()
    if detail:
        detail = "\n".join(detail.splitlines()[-20:])[-4000:]
        raise RuntimeError(
            f"Nginx {label} failed (exit {result.returncode}):\n{detail}"
        )
    raise RuntimeError(f"Nginx {label} failed (exit {result.returncode})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = load_config()
    verify_listen_address(config["proxy"]["listen_address"])
    client, token = connect_openbao()
    try:
        secret = ensure_web_secret(client, token)
        file = prepare_runtime(config, secret)
    finally:
        client.revoke_self(token)
    if args.prepare_only:
        print("[OK] Nginx configuration and credentials prepared; no containers started")
        return
    networks = [NETBOX_NETWORK, OXIDIZED_NETWORK]
    if "zabbix" in config["services"]:
        networks.append(ZABBIX_NETWORK)
    for network in networks:
        ensure_network(network)
    compose_step(file, "Compose validation", "config", "--quiet")
    compose_step(file, "image pull", "pull")
    compose_step(
        file,
        "configuration test",
        "run", "--rm", "--no-deps", "proxy",
        "nginx", "-c", "/etc/atlas/nginx.conf", "-t",
    )
    # Recreate to reload certificates/hash/config, including renewed certificates.
    compose_step(file, "container start", "up", "-d", "--force-recreate")
    wait_healthy(file, "atlas-proxy", "proxy", 90)
    for name, service in config["services"].items():
        print(f"[OK] {name}: https://{service['hostname']}:{config['proxy']['https_port']}")
    print("[OK] Oxidized credentials: OpenBao atlas/oxidized/web (values not printed)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
