"""Shared deployment primitives; never print secret values or command input."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from atlas.openbao_client import OpenBaoClient, OpenBaoNotFound

ROOT = Path(__file__).resolve().parent.parent
NETBOX_NETWORK = "atlas-netbox-web"
OXIDIZED_NETWORK = "atlas-oxidized-web"
OPENBAO_NETWORK = "atlas-openbao-api"


def run(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    if result.returncode:
        # Docker/OpenSSL errors can include command input or environment values.
        raise RuntimeError(f"{command[0]} operation failed (exit {result.returncode})")
    return result.stdout.strip()


def write_private(path, content, mode=0o600, group_id=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        if group_id is not None:
            os.fchown(descriptor, -1, int(group_id))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_yaml(path, data):
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def ensure_network(name):
    result = subprocess.run(
        ["docker", "network", "inspect", name], text=True, capture_output=True,
    )
    if result.returncode:
        run(["docker", "network", "create", "--driver", "bridge", name])
        return
    network = json.loads(result.stdout)[0]
    if network["Driver"] != "bridge" or network.get("Internal"):
        raise RuntimeError(f"Network {name} must be an ordinary bridge network")
    # Do not permit an externally routed network to bypass the proxy.
    options = network.get("Options") or {}
    if any("gateway_mode" in key and value != "nat" for key, value in options.items()):
        raise RuntimeError(f"Network {name} must use NAT port isolation")
    if options.get("com.docker.network.bridge.trusted_host_interfaces"):
        raise RuntimeError(f"Network {name} must not permit direct routing")


def compose(file, project, *args):
    return run(["docker", "compose", "-p", project, "-f", str(file), *args])


def wait_healthy(file, project, service, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container = compose(file, project, "ps", "-q", service)
        if container:
            state = json.loads(run(["docker", "inspect", container]))[0]["State"]
            if state.get("Health", {}).get("Status") == "healthy":
                return
        time.sleep(2)
    raise RuntimeError(f"{service} did not become healthy within {timeout}s")


def connect_openbao():
    client = OpenBaoClient(os.environ.get("OPENBAO_URL", "http://127.0.0.1:18200"))
    status = client.seal_status()
    if not status.get("initialized") or status.get("sealed"):
        raise RuntimeError("OpenBao must be initialized and unsealed")
    token = client.login_from_identity(ROOT / ".runtime/openbao-approle.json")
    return client, token


def optional_secret(client, token, path):
    try:
        return client.kv_read(token, path)
    except OpenBaoNotFound:
        return None
