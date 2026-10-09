#!/usr/bin/env python3
"""Display the Zabbix administrator login for an authorized local operator."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas.deployment import connect_openbao  # noqa: E402
from atlas.proxy import load_config, public_service_urls  # noqa: E402


def load_admin_credentials():
    client, token = connect_openbao()
    try:
        secret = client.kv_read(token, "zabbix/admin")
    finally:
        client.revoke_self(token)

    username = secret.get("username") if isinstance(secret, dict) else None
    password = secret.get("password") if isinstance(secret, dict) else None
    if (
        username != "Admin"
        or not isinstance(password, str)
        or len(password) < 32
        or any(character in password for character in "\r\n\x00")
    ):
        raise RuntimeError("Invalid OpenBao atlas/zabbix/admin secret")
    return username, password


def main():
    config = load_config()
    username, password = load_admin_credentials()
    print(f"URL: {public_service_urls(config)['zabbix']}")
    print(f"Username: {username}")
    print(f"Password: {password}")
    print("[WARN] Password displayed for this local administrator session; do not log or share it.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
