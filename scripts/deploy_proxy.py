#!/usr/bin/env python3
"""Deploy shared Nginx HTTPS ingress using customer-provided CA certificates."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    NETBOX_NETWORK, OXIDIZED_NETWORK, compose, connect_openbao,
    ensure_network, wait_healthy,
)
from atlas.proxy import ensure_web_secret, load_config, prepare_runtime  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = load_config()
    client, token = connect_openbao()
    try:
        secret = ensure_web_secret(client, token)
        file = prepare_runtime(config, secret)
    finally:
        client.revoke_self(token)
    if args.prepare_only:
        print("[OK] Nginx configuration and credentials prepared; no containers started")
        return
    for network in (NETBOX_NETWORK, OXIDIZED_NETWORK):
        ensure_network(network)
    compose(file, "atlas-proxy", "config", "--quiet")
    compose(file, "atlas-proxy", "pull")
    compose(file, "atlas-proxy", "run", "--rm", "--no-deps", "proxy",
            "nginx", "-c", "/etc/atlas/nginx.conf", "-t")
    # Recreate to reload certificates/hash/config, including renewed certificates.
    compose(file, "atlas-proxy", "up", "-d", "--force-recreate")
    wait_healthy(file, "atlas-proxy", "proxy", 90)
    for name, service in config["services"].items():
        print(f"[OK] {name}: https://{service['hostname']}:{config['proxy']['https_port']}")
    print("[OK] Oxidized credentials: OpenBao atlas/oxidized/web (values not printed)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
