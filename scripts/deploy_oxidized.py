#!/usr/bin/env python3
"""Deploy and reconcile the Atlas-managed Oxidized runtime."""

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    OXIDIZED_NETWORK,
    compose,
    ensure_network,
    wait_healthy,
    write_private,
    write_yaml,
)
from atlas.device_credentials import (  # noqa: E402
    DeviceValidationError,
    resolve_oxidized_device,
)
from atlas.openbao_client import OpenBaoClient  # noqa: E402
from atlas.oxidized_inventory import (  # noqa: E402
    canonical_inventory,
    read_inventory as read_inventory_snapshot,
    render_inventory as render_canonical_inventory,
    validate_inventory as validate_canonical_inventory,
    write_inventory as write_canonical_inventory,
)
from atlas.services.netbox import NetBoxService  # noqa: E402
from atlas.services.errors import DependencyError  # noqa: E402
from atlas.services.openbao import OpenBaoService  # noqa: E402
from atlas.services.oxidized import OxidizedService  # noqa: E402
from atlas.services.oxidized_sync import (  # noqa: E402
    FileInventoryStore,
    OxidizedSyncService,
    resolved_inventory_entry,
)


OXIDIZED_CONFIG_PATH = "/etc/oxidized/config"
OXIDIZED_SOURCE_PATH = "/etc/atlas-oxidized/router.json"
OXIDIZED_WAIT_ENTRYPOINT = "/etc/atlas-oxidized-runtime/entrypoint.sh"
OXIDIZED_WAIT_SERVER = "/etc/atlas-oxidized-runtime/waiting_server.rb"
OXIDIZED_RUNNER = "/etc/service/oxidized/run"
OXIDIZED_WEB_URL = "http://127.0.0.1:8888"
DEFAULT_INVENTORY_DIRECTORY = "/run/atlas/oxidized"
INVENTORY_FILE_NAME = "router.json"
BACKEND_IDENTITY = ROOT / ".runtime/openbao-backend.json"


WAIT_ENTRYPOINT = r'''#!/bin/sh
set -eu

SOURCE="/etc/atlas-oxidized/router.json"
WAIT_SERVER="/etc/atlas-oxidized-runtime/waiting_server.rb"
WAIT_PID=""

has_inventory() {
    ruby -rjson -e '
      data = JSON.parse(File.read(ARGV.fetch(0)))
      exit(data.is_a?(Array) && !data.empty? ? 0 : 1)
    ' "$SOURCE" 2>/dev/null
}

cleanup() {
    if [ -n "${WAIT_PID}" ] && kill -0 "${WAIT_PID}" 2>/dev/null; then
        kill "${WAIT_PID}" 2>/dev/null || true
        wait "${WAIT_PID}" 2>/dev/null || true
    fi
}

trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT

if ! has_inventory; then
    echo "[INFO] Oxidized waiting for device inventory"
    ruby "$WAIT_SERVER" &
    WAIT_PID=$!

    while ! has_inventory; do
        if ! kill -0 "$WAIT_PID" 2>/dev/null; then
            echo "[ERROR] Oxidized waiting server stopped unexpectedly" >&2
            wait "$WAIT_PID" || true
            exit 1
        fi
        sleep 2
    done

    echo "[INFO] Device inventory detected; starting native Oxidized"
    cleanup
    WAIT_PID=""
fi

trap - INT TERM EXIT
exec /usr/bin/dumb-init -- runsvdir -P /etc/service
'''


WAIT_SERVER = r'''require "socket"

HOST = "0.0.0.0"
PORT = 8888

HTML = <<~HTML.freeze
  <!doctype html>
  <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>Oxidized waiting for inventory</title>
      <style>
        body { font-family: sans-serif; margin: 3rem; color: #222; }
        h1 { font-size: 1.4rem; }
        p { max-width: 48rem; line-height: 1.5; }
      </style>
    </head>
    <body>
      <h1>Oxidized is ready and waiting for inventory.</h1>
      <p>No devices are currently configured for configuration backup.</p>
      <p>Add an eligible device to the Atlas inventory to start collection.</p>
    </body>
  </html>
HTML

server = TCPServer.new(HOST, PORT)

shutdown = proc do
  begin
    server.close
  rescue StandardError
    nil
  end
  exit 0
end

trap("TERM", &shutdown)
trap("INT", &shutdown)

loop do
  client = server.accept
  begin
    request_line = client.gets.to_s
    path = request_line.split[1] || "/"

    while (line = client.gets)
      break if line == "\r\n"
    end

    if path.start_with?("/nodes.json")
      status = "200 OK"
      content_type = "application/json"
      body = "[]\n"
    elsif path.start_with?("/reload")
      status = "503 Service Unavailable"
      content_type = "application/json"
      body = "{\"status\":\"waiting_for_inventory\"}\n"
    else
      status = "200 OK"
      content_type = "text/html; charset=utf-8"
      body = HTML
    end

    client.write "HTTP/1.1 #{status}\r\n"
    client.write "Content-Type: #{content_type}\r\n"
    client.write "Content-Length: #{body.bytesize}\r\n"
    client.write "Connection: close\r\n"
    client.write "Cache-Control: no-store\r\n"
    client.write "\r\n"
    client.write body
  rescue Errno::EPIPE, Errno::ECONNRESET
    nil
  ensure
    client.close rescue nil
  end
end
'''


OXIDIZED_RUN_SCRIPT = r'''#!/bin/sh
set -eu

SOURCE="/etc/atlas-oxidized/router.json"
INVENTORY_DIRECTORY="/etc/atlas-oxidized"
INVENTORY_GID="$(stat -c '%g' "$SOURCE")"
DIRECTORY_GID="$(stat -c '%g' "$INVENTORY_DIRECTORY")"

case "$INVENTORY_GID" in
    *[!0-9]*|"")
        echo "[ERROR] Invalid Oxidized inventory group ID" >&2
        exit 1
        ;;
esac

if [ "$DIRECTORY_GID" != "$INVENTORY_GID" ]; then
    echo "[ERROR] Oxidized inventory directory and file group IDs differ" >&2
    exit 1
fi

CONFIG_HOME="/home/oxidized/.config/oxidized"
[ -d "$CONFIG_HOME" ] || mkdir -p "$CONFIG_HOME"
[ ! -f "$CONFIG_HOME/pid" ] || rm -f "$CONFIG_HOME/pid"
chown -R oxidized:oxidized "$CONFIG_HOME"

# The upstream runit service invokes `gosu oxidized`, which rebuilds the
# supplementary group list from /etc/group and discards Docker group_add.
# Resolve the bind mount's container-visible GID and use it as the process
# primary group. Host and container GIDs are not assumed to be identical.
exec gosu "oxidized:${INVENTORY_GID}" oxidized
'''


RUNTIME_VALIDATION_SCRIPT = r'''set -eu

fail() {
    echo "[ERROR] $1" >&2
    exit 1
}

SOURCE="/etc/atlas-oxidized/router.json"
INVENTORY_DIRECTORY="/etc/atlas-oxidized"

test -x /usr/bin/dumb-init || fail "dumb-init is unavailable"
command -v runsvdir >/dev/null || fail "runsvdir is unavailable"
command -v gosu >/dev/null || fail "gosu is unavailable"
command -v stat >/dev/null || fail "stat is unavailable"
test -x /etc/atlas-oxidized-runtime/entrypoint.sh || fail "Atlas entrypoint is not executable"
test -r /etc/atlas-oxidized-runtime/waiting_server.rb || fail "Atlas waiting server is unreadable"
test -x __OXIDIZED_RUNNER__ || fail "Atlas Oxidized runner is not executable"

INVENTORY_GID="$(stat -c '%g' "$SOURCE")"
DIRECTORY_GID="$(stat -c '%g' "$INVENTORY_DIRECTORY")"

case "$INVENTORY_GID" in
    *[!0-9]*|"") fail "Invalid container-visible inventory group ID" ;;
esac

if [ "$DIRECTORY_GID" != "$INVENTORY_GID" ]; then
    fail "Oxidized inventory directory and file group IDs differ"
fi

gosu "oxidized:${INVENTORY_GID}" /bin/sh -ec '
    test -r /etc/oxidized/config || {
        echo "[ERROR] Oxidized user cannot read its configuration" >&2
        exit 1
    }
    test -r /etc/atlas-oxidized/router.json || {
        echo "[ERROR] Oxidized user cannot read router.json" >&2
        exit 1
    }
    test -r /home/oxidized/.ssh/known_hosts || {
        echo "[ERROR] Oxidized user cannot read known_hosts" >&2
        exit 1
    }
'
'''.replace("__OXIDIZED_RUNNER__", OXIDIZED_RUNNER)


def load_config():
    return yaml.safe_load(
        (ROOT / "deployment/oxidized.yaml").read_text(encoding="utf-8")
    )


def persistent_runtime_path(config, root=None):
    base = Path(root) if root is not None else ROOT
    return base / config["runtime"]["directory"]


def inventory_directory(config, root=None):
    override = os.environ.get("ATLAS_OXIDIZED_RUN_DIR")
    configured = override or config["runtime"].get(
        "inventory_directory",
        DEFAULT_INVENTORY_DIRECTORY,
    )
    path = Path(configured)

    if override:
        return path

    if root is not None and path.is_absolute():
        return Path(root) / path.relative_to("/")

    if path.is_absolute():
        return path

    base = Path(root) if root is not None else ROOT
    return base / path


def inventory_path(config, root=None):
    return inventory_directory(config, root=root) / INVENTORY_FILE_NAME


def validate_inventory(data):
    return validate_canonical_inventory(data)


def render_inventory(data):
    return render_canonical_inventory(data)


def write_inventory(path, data):
    write_canonical_inventory(path, data)


def read_inventory(path):
    return list(read_inventory_snapshot(path).entries)


def has_inventory(source):
    return bool(read_inventory(source))


def prepare_inventory(config, root=None):
    directory = inventory_directory(config, root=root)

    if directory.is_symlink():
        raise RuntimeError(f"Oxidized inventory directory cannot be a symlink: {directory}")

    directory.mkdir(parents=True, exist_ok=True)
    # Keep credentials group-readable only. The container resolves the mounted
    # file's group ID from inside its own namespace before dropping privileges.
    os.chmod(directory, 0o750)
    directory_gid = directory.stat().st_gid

    source = directory / INVENTORY_FILE_NAME

    if source.is_symlink():
        raise RuntimeError(f"Oxidized inventory cannot be a symlink: {source}")

    if source.exists():
        if not source.is_file():
            raise RuntimeError(f"Oxidized inventory must be a regular file: {source}")
        read_inventory(source)
        if source.stat().st_gid != directory_gid:
            try:
                os.chown(source, -1, directory_gid)
            except PermissionError as exc:
                raise RuntimeError(
                    "Oxidized inventory group does not match its directory and "
                    "could not be corrected"
                ) from exc
        os.chmod(source, 0o640)
    else:
        write_inventory(source, [])

    return source


def canonical_oxidized_config(settings):
    """Return the Atlas-owned Oxidized 0.37.0 configuration."""
    home = "/home/oxidized/.config/oxidized"

    return {
        "interval": int(settings["interval"]),
        "threads": int(settings["threads"]),
        "timeout": int(settings["timeout"]),
        "retries": 3,
        "resolve_dns": False,
        "input": {
            "default": "ssh",
            "ssh": {
                "secure": False,
            },
        },
        "output": {
            "default": "git",
            "clean_obsolete_nodes": False,
            "git": {
                "user": "Atlas Oxidized",
                "email": "oxidized@localhost",
                "repo": f"{home}/repository.git",
            },
        },
        "source": {
            "default": "jsonfile",
            "jsonfile": {
                "file": OXIDIZED_SOURCE_PATH,
                "map": {
                    "name": "name",
                    "ip": "ip",
                    "model": "model",
                    "username": "username",
                    "password": "password",
                },
                "vars_map": {
                    "enable": "enable",
                },
            },
        },
        "extensions": {
            "oxidized-web": {
                "load": True,
                "listen": "0.0.0.0",
                "port": 8888,
                "hide_node_vars": ["enable", "password"],
            },
        },
    }


def write_text(path, content, mode):
    write_private(path, content, mode=mode)


def prepare_known_hosts(config, root=None):
    runtime = persistent_runtime_path(config, root=root)
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)

    ssh_dir = runtime / "ssh"

    if ssh_dir.is_symlink():
        raise RuntimeError(f"Oxidized SSH directory cannot be a symlink: {ssh_dir}")

    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o755)

    known_hosts = ssh_dir / "known_hosts"

    if known_hosts.is_symlink():
        raise RuntimeError(f"Oxidized known_hosts cannot be a symlink: {known_hosts}")

    if not known_hosts.exists():
        write_private(known_hosts, "", mode=0o644)
    else:
        if not known_hosts.is_file():
            raise RuntimeError(
                f"Oxidized known_hosts must be a regular file: {known_hosts}"
            )
        os.chmod(known_hosts, 0o644)

    return known_hosts


def prepare_runtime(config, root=None):
    runtime = persistent_runtime_path(config, root=root)
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)

    source = prepare_inventory(config, root=root)
    settings = config["oxidized"]

    settings_file = runtime / "config"
    write_yaml(settings_file, canonical_oxidized_config(settings))
    os.chmod(settings_file, 0o644)

    legacy_source = runtime / "router.db"
    if legacy_source.exists() or legacy_source.is_symlink():
        legacy_source.unlink()

    ssh_dir = prepare_known_hosts(config, root=root).parent

    entrypoint = runtime / "entrypoint.sh"
    write_text(entrypoint, WAIT_ENTRYPOINT, 0o755)

    wait_server = runtime / "waiting_server.rb"
    write_text(wait_server, WAIT_SERVER, 0o644)

    oxidized_runner = runtime / "oxidized-run.sh"
    write_text(oxidized_runner, OXIDIZED_RUN_SCRIPT, 0o755)

    compose_file = runtime / "docker-compose.yml"
    write_yaml(
        compose_file,
        {
            "services": {
                "oxidized": {
                    "image": settings["docker_image"],
                    "restart": "unless-stopped",
                    "entrypoint": [
                        "/bin/sh",
                        OXIDIZED_WAIT_ENTRYPOINT,
                    ],
                    "volumes": [
                        "atlas_oxidized_data:/home/oxidized/.config/oxidized",
                        f"{source.parent}:/etc/atlas-oxidized:ro",
                        f"{settings_file}:{OXIDIZED_CONFIG_PATH}:ro",
                        f"{ssh_dir}:/home/oxidized/.ssh:ro",
                        f"{entrypoint}:{OXIDIZED_WAIT_ENTRYPOINT}:ro",
                        f"{wait_server}:{OXIDIZED_WAIT_SERVER}:ro",
                        f"{oxidized_runner}:{OXIDIZED_RUNNER}:ro",
                    ],
                    "networks": {
                        "web": {
                            "aliases": ["atlas-oxidized"],
                        },
                    },
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "curl",
                            "--fail",
                            "--silent",
                            f"{OXIDIZED_WEB_URL}/nodes.json",
                        ],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 20,
                        "start_period": "10s",
                    },
                },
            },
            "networks": {
                "web": {
                    "external": True,
                    "name": OXIDIZED_NETWORK,
                },
            },
            "volumes": {
                "atlas_oxidized_data": {
                    "name": "atlas-oxidized-data",
                },
            },
        },
    )
    os.chmod(compose_file, 0o600)

    return compose_file


def get_netbox_url():
    config = yaml.safe_load(
        (ROOT / "deployment/netbox.yaml").read_text(encoding="utf-8")
    )
    address = str(config["network"]["listen_address"])
    if address in {"0.0.0.0", "::"}:
        address = "127.0.0.1"
    return f"http://{address}:{int(config['network']['port'])}"


def build_inventory(devices, openbao_client, openbao_token):
    inventory = []

    for device in devices:
        try:
            resolved = resolve_oxidized_device(
                device,
                openbao_client,
                openbao_token,
            )
        except DeviceValidationError as exc:
            name = device.get("name") or "<unnamed>"
            raise RuntimeError(f"Invalid Oxidized device {name}: {exc}") from None

        if resolved is None:
            continue

        inventory.append(resolved_inventory_entry(resolved))

    return canonical_inventory(inventory)


def build_runtime_services(source):
    client = OpenBaoClient(
        os.environ.get("OPENBAO_URL", "http://127.0.0.1:18200"),
        timeout=(3, 10),
    )
    openbao = OpenBaoService(client, BACKEND_IDENTITY)
    netbox = NetBoxService(get_netbox_url(), openbao, 3, 10)
    return openbao, netbox, FileInventoryStore(source)


def print_sync_plan(plan):
    for issue in plan.issues:
        print(f"[WARN] {issue.device}: {issue.code}")
    print(
        "[OK] Oxidized desired inventory resolved: "
        f"{len(plan.desired)} device(s); "
        f"added={plan.summary.added} updated={plan.summary.updated} "
        f"removed={plan.summary.removed} skipped={plan.summary.skipped} "
        f"errors={plan.summary.errors}"
    )


def load_desired_inventory(source):
    openbao, netbox, store = build_runtime_services(source)
    service = OxidizedSyncService(netbox, openbao, None, store)
    plan = service.build_plan(store.read())
    print_sync_plan(plan)
    return list(plan.desired)


def compose_exec(file, project, *args, capture=False, check=True):
    command = [
        "docker",
        "compose",
        "-f",
        str(file),
        "-p",
        project,
        *args,
    ]

    result = subprocess.run(
        command,
        cwd=file.parent,
        text=True,
        capture_output=capture,
    )

    if check and result.returncode != 0:
        raise RuntimeError("Docker Compose operation failed")

    return result


def validate_runtime(file, project):
    compose(file, project, "config", "--quiet")

    result = compose_exec(
        file,
        project,
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "/bin/sh",
        "oxidized",
        "-ec",
        RUNTIME_VALIDATION_SCRIPT,
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        details = "\n".join(
            output.strip()
            for output in (result.stdout, result.stderr)
            if output and output.strip()
        )
        message = "Oxidized runtime preflight failed"
        if details:
            message = f"{message}: {details}"
        raise RuntimeError(message)

    print("[OK] Oxidized runtime mounts validated")


class ComposeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return json.loads(self.body)


class ComposeRequester:
    """Run the shared Oxidized HTTP client from its private Docker network."""

    RUBY_REQUEST = r'''
require "json"
require "net/http"
require "uri"
uri = URI(ARGV.fetch(0))
connect_timeout = Float(ARGV.fetch(1))
read_timeout = Float(ARGV.fetch(2))
request = Net::HTTP::Get.new(uri)
request["Accept"] = "application/json"
response = Net::HTTP.start(
  uri.host,
  uri.port,
  open_timeout: connect_timeout,
  read_timeout: read_timeout,
) { |http| http.request(request) }
STDOUT.write(JSON.generate({"status" => response.code.to_i, "body" => response.body}))
'''

    def __init__(self, file, project):
        self.file = file
        self.project = project

    def get(self, url, *, headers, timeout):
        del headers
        connect_timeout, read_timeout = timeout
        result = compose_exec(
            self.file,
            self.project,
            "exec",
            "-T",
            "oxidized",
            "ruby",
            "-e",
            self.RUBY_REQUEST,
            url,
            str(connect_timeout),
            str(read_timeout),
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            raise requests.ConnectionError("Oxidized request failed")
        try:
            payload = json.loads(result.stdout)
            return ComposeResponse(int(payload["status"]), payload["body"])
        except (KeyError, TypeError, ValueError):
            raise requests.ConnectionError("Invalid Docker response") from None


def reload_inventory(file, project, inventory, timeout):
    deadline = time.monotonic() + int(timeout)
    service = OxidizedService(
        OXIDIZED_WEB_URL,
        connect_timeout=3,
        read_timeout=min(max(int(timeout), 1), 10),
        requester=ComposeRequester(file, project),
    )

    while time.monotonic() < deadline:
        try:
            service.reload_inventory(inventory)
        except DependencyError as exc:
            if exc.reason in {"authentication_failed", "invalid_response"}:
                break
        else:
            if inventory:
                print("[OK] Oxidized inventory reloaded")
            else:
                print("[OK] Oxidized remains healthy with empty inventory")
            return

        time.sleep(2)

    raise RuntimeError(
        "Oxidized container is running but inventory reload did not complete"
    )


def apply_inventory(source, inventory, reload_callback):
    write_inventory(source, inventory)
    reload_callback()


class ComposeOxidizedReloader:
    def __init__(self, file, project, timeout):
        self.file = file
        self.project = project
        self.timeout = timeout

    def reload_inventory(self, inventory):
        reload_inventory(
            self.file,
            self.project,
            inventory,
            self.timeout,
        )


def require_running_container(file, project):
    container = compose(file, project, "ps", "-q", "oxidized")
    if not container:
        raise RuntimeError(
            "Oxidized container is not running; deploy the stack before reconcile"
        )

    result = compose_exec(
        file,
        project,
        "exec",
        "-T",
        "oxidized",
        "test",
        "-r",
        OXIDIZED_SOURCE_PATH,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Running Oxidized container does not use the JSON runtime; "
            "run atlasctl deploy once before reconcile"
        )


def reconcile_running(config, file, source):
    project = config["runtime"]["compose_project"]
    timeout = int(config["runtime"]["startup_timeout"])
    require_running_container(file, project)
    openbao, netbox, store = build_runtime_services(source)
    service = OxidizedSyncService(
        netbox,
        openbao,
        ComposeOxidizedReloader(file, project, timeout),
        store,
    )
    result = service.sync()
    print(
        "[OK] Oxidized reconcile: "
        f"status={result.status} changed={result.inventory_changed} "
        f"reloaded={result.oxidized_reloaded}"
    )
    if result.status == "error":
        raise RuntimeError("Oxidized inventory was written but reload failed")
    return result


def deploy_runtime(config, file, source, inventory):
    project = config["runtime"]["compose_project"]
    timeout = int(config["runtime"]["startup_timeout"])

    write_inventory(source, inventory)
    ensure_network(OXIDIZED_NETWORK)
    compose(file, project, "config", "--quiet")
    compose(file, project, "pull")
    validate_runtime(file, project)
    compose(file, project, "up", "-d", "--force-recreate", "oxidized")
    wait_healthy(file, project, "oxidized", timeout)
    reload_inventory(file, project, inventory, timeout)

    if inventory:
        print("[OK] Oxidized ready on its private container network")
    else:
        print("[OK] Oxidized container is healthy and waiting for device inventory")


def validate_host(host):
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    if len(host) > 253 or not re.fullmatch(
        r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*",
        host,
    ):
        raise RuntimeError("Invalid SSH host name or address")

    return host


def validate_port(port):
    port = int(port)
    if not 1 <= port <= 65535:
        raise RuntimeError("SSH port must be between 1 and 65535")
    return port


def host_lookup_name(host, port):
    return host if port == 22 else f"[{host}]:{port}"


def key_record(line):
    fields = line.split()
    if len(fields) < 3:
        raise RuntimeError("Invalid SSH host key data")

    try:
        key_blob = base64.b64decode(fields[2], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("Invalid SSH host public key") from exc

    digest = base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii")
    return {
        "line": line,
        "key_type": fields[1],
        "fingerprint": f"SHA256:{digest.rstrip('=')}",
    }


def scan_host_keys_with_legacy_ssh(host, port=22):
    host = validate_host(host)
    port = validate_port(port)

    with tempfile.TemporaryDirectory(prefix="atlas-host-key-") as temporary:
        known_hosts = Path(temporary) / "known_hosts"
        subprocess.run(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-oBatchMode=yes",
                "-oPreferredAuthentications=none",
                "-oStrictHostKeyChecking=accept-new",
                f"-oUserKnownHostsFile={known_hosts}",
                "-oGlobalKnownHostsFile=/dev/null",
                "-oHashKnownHosts=no",
                "-oKexAlgorithms=+diffie-hellman-group1-sha1",
                "-oHostKeyAlgorithms=+ssh-rsa",
                "-oCiphers=+aes128-cbc",
                "-oConnectTimeout=5",
                "-oNumberOfPasswordPrompts=0",
                "-p",
                str(port),
                "-l",
                "atlas-host-key-scan",
                host,
                "exit",
            ],
            text=True,
            capture_output=True,
        )
        if not known_hosts.exists():
            raise RuntimeError(
                f"No SSH host key received from {host}:{port} "
                "with legacy SSH compatibility enabled"
            )
        lines = [
            line.strip()
            for line in known_hosts.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    if not lines:
        raise RuntimeError(
            f"No SSH host key received from {host}:{port} "
            "with legacy SSH compatibility enabled"
        )
    return [key_record(line) for line in lines]


def scan_host_keys(host, port=22, legacy_ssh=False):
    if legacy_ssh:
        return scan_host_keys_with_legacy_ssh(host, port)

    host = validate_host(host)
    port = validate_port(port)
    result = subprocess.run(
        ["ssh-keyscan", "-T", "5", "-p", str(port), host],
        text=True,
        capture_output=True,
    )
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines:
        raise RuntimeError(f"No SSH host key received from {host}:{port}")
    return [key_record(line) for line in lines]


def trusted_host_keys(path, host, port=22):
    lookup = host_lookup_name(validate_host(host), validate_port(port))
    result = subprocess.run(
        ["ssh-keygen", "-F", lookup, "-f", str(path)],
        text=True,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Unable to inspect persistent Oxidized known_hosts")
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return [key_record(line) for line in lines]


def accept_host_key(path, host, fingerprint, port=22, legacy_ssh=False):
    scanned = scan_host_keys(host, port, legacy_ssh=legacy_ssh)
    selected = next(
        (record for record in scanned if record["fingerprint"] == fingerprint),
        None,
    )
    if selected is None:
        raise RuntimeError("Requested fingerprint was not presented by the device")

    existing = trusted_host_keys(path, host, port)
    if any(record["fingerprint"] == fingerprint for record in existing):
        return "already_trusted"

    conflicts = [
        record
        for record in existing
        if record["key_type"] == selected["key_type"]
    ]
    if conflicts:
        raise RuntimeError(
            "SSH host-key mismatch; existing trust was not modified"
        )

    current = Path(path).read_text(encoding="utf-8")
    if current and not current.endswith("\n"):
        current += "\n"
    write_private(path, current + selected["line"] + "\n", mode=0o644)
    return "accepted"


def print_scanned_host_keys(host, port, legacy_ssh=False):
    records = scan_host_keys(host, port, legacy_ssh=legacy_ssh)
    print(f"SSH host keys presented by {host}:{port}:")
    for record in records:
        print(f"  {record['key_type']} {record['fingerprint']}")
    print("No key was trusted. Verify a fingerprint independently before acceptance.")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--reconcile-only", action="store_true")
    mode.add_argument("--host-key-scan", metavar="HOST")
    mode.add_argument(
        "--host-key-accept",
        nargs=2,
        metavar=("HOST", "SHA256_FINGERPRINT"),
    )
    parser.add_argument(
        "--ci-prepared-inventory",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--legacy-ssh",
        action="store_true",
        help=(
            "enable legacy group1/ssh-rsa/aes128-cbc compatibility only "
            "while retrieving a host key"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()

    if args.host_key_scan:
        prepare_known_hosts(config)
        print_scanned_host_keys(
            args.host_key_scan,
            args.port,
            legacy_ssh=args.legacy_ssh,
        )
        return

    if args.host_key_accept:
        known_hosts = prepare_known_hosts(config)
        result = accept_host_key(
            known_hosts,
            args.host_key_accept[0],
            args.host_key_accept[1],
            args.port,
            legacy_ssh=args.legacy_ssh,
        )
        print(f"[OK] SSH host key {result.replace('_', ' ')}")
        return

    file = prepare_runtime(config)
    source = inventory_path(config)

    if args.prepare_only:
        print("[OK] Oxidized runtime prepared; no containers started")
        return

    if args.ci_prepared_inventory:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise RuntimeError(
                "--ci-prepared-inventory is restricted to GitHub Actions"
            )
        if args.reconcile_only:
            raise RuntimeError(
                "CI prepared inventory cannot be used for reconciliation"
            )
        inventory = read_inventory(source)
    else:
        if args.reconcile_only:
            reconcile_running(config, file, source)
            return
        inventory = load_desired_inventory(source)

    if args.reconcile_only:
        raise RuntimeError("Prepared CI inventory cannot be reconciled")

    deploy_runtime(config, file, source, inventory)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
