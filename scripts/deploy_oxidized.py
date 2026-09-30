#!/usr/bin/env python3
"""Deploy and reconcile the Atlas-managed Oxidized runtime."""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    OXIDIZED_NETWORK,
    compose,
    ensure_network,
    wait_healthy,
    write_yaml,
)

OXIDIZED_CONFIG_PATH = "/etc/oxidized/config"
OXIDIZED_SOURCE_PATH = "/etc/atlas-oxidized/router.db"
OXIDIZED_WAIT_ENTRYPOINT = "/etc/atlas-oxidized/entrypoint.sh"
OXIDIZED_WAIT_SERVER = "/etc/atlas-oxidized/waiting_server.rb"
OXIDIZED_WEB_URL = "http://127.0.0.1:8888"


WAIT_ENTRYPOINT = r'''#!/bin/sh
set -eu

SOURCE="/etc/atlas-oxidized/router.db"
WAIT_SERVER="/etc/atlas-oxidized/waiting_server.rb"
WAIT_PID=""

has_inventory() {
    grep -Ev '^[[:space:]]*(#|$)' "$SOURCE" 2>/dev/null | grep -q .
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


def has_inventory(source):
    """Return True when router.db contains at least one usable node line."""
    if not source.exists():
        return False

    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True

    return False


def canonical_oxidized_config(settings):
    """Return the Atlas-owned Oxidized configuration."""
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
                # Strict host-key verification. Trust is persisted in the
                # Atlas-managed known_hosts file mounted below.
                "secure": True,
            },
        },
        "output": {
            "default": "git",
            "git": {
                "user": "Atlas Oxidized",
                "email": "oxidized@localhost",
                "repo": f"{home}/repository.git",
            },
        },
        "source": {
            "default": "csv",
            "csv": {
                "file": OXIDIZED_SOURCE_PATH,
                "delimiter": ":",
                # Transitional Atlas CSV schema:
                # name:model:username:password:enable
                # The future NetBox/OpenBao synchronizer should render this
                # runtime source; plaintext credentials must never be committed.
                "map": {
                    "name": 0,
                    "model": 1,
                    "username": 2,
                    "password": 3,
                },
                "vars_map": {
                    "enable": 4,
                },
            },
        },
        "extensions": {
            "oxidized-web": {
                "load": True,
                "listen": "0.0.0.0",
                "port": 8888,
            },
        },
    }


def write_text(path, content, mode):
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def prepare_runtime(config, root=None):
    base = root or ROOT
    runtime = base / config["runtime"]["directory"]
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)

    settings = config["oxidized"]

    settings_file = runtime / "config"
    write_yaml(settings_file, canonical_oxidized_config(settings))
    os.chmod(settings_file, 0o644)

    source = runtime / "router.db"
    if not source.exists():
        source.touch()
    # router.db may temporarily contain credentials, but the enclosing runtime
    # directory is 0700. The file itself must be readable by UID 30000 in the
    # Oxidized container through the read-only bind mount.
    os.chmod(source, 0o644)

    ssh_dir = runtime / "ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o755)

    known_hosts = ssh_dir / "known_hosts"
    if not known_hosts.exists():
        known_hosts.touch()
    os.chmod(known_hosts, 0o644)

    entrypoint = runtime / "entrypoint.sh"
    write_text(entrypoint, WAIT_ENTRYPOINT, 0o755)

    wait_server = runtime / "waiting_server.rb"
    write_text(wait_server, WAIT_SERVER, 0o644)

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
                        f"{source}:{OXIDIZED_SOURCE_PATH}:ro",
                        f"{settings_file}:{OXIDIZED_CONFIG_PATH}:ro",
                        f"{ssh_dir}:/home/oxidized/.ssh:ro",
                        f"{entrypoint}:{OXIDIZED_WAIT_ENTRYPOINT}:ro",
                        f"{wait_server}:{OXIDIZED_WAIT_SERVER}:ro",
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
        if capture and result.stdout:
            print(result.stdout.rstrip())
        if capture and result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError(
            "Command failed: " + " ".join(command)
        )

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
        (
            "test -x /usr/bin/dumb-init; "
            "command -v runsvdir >/dev/null; "
            "test -r /etc/oxidized/config; "
            "test -r /etc/atlas-oxidized/router.db; "
            "test -r /home/oxidized/.ssh/known_hosts; "
            "test -x /etc/atlas-oxidized/entrypoint.sh; "
            "test -r /etc/atlas-oxidized/waiting_server.rb"
        ),
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError(
            "Oxidized container cannot read one or more Atlas runtime mounts"
        )

    print("[OK] Oxidized runtime mounts validated")


def reload_native_oxidized(file, project):
    result = compose_exec(
        file,
        project,
        "exec",
        "-T",
        "oxidized",
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "3",
        f"{OXIDIZED_WEB_URL}/reload?format=json",
        capture=True,
        check=False,
    )
    return result.returncode == 0


def wait_for_native_and_reload(file, project, timeout):
    deadline = time.monotonic() + int(timeout)

    while time.monotonic() < deadline:
        if reload_native_oxidized(file, project):
            print("[OK] Oxidized inventory reloaded")
            return
        time.sleep(2)

    raise RuntimeError(
        "Oxidized container is running but the native web/API backend "
        "did not become ready before the startup timeout"
    )


def deploy_or_reconcile(config, file):
    project = config["runtime"]["compose_project"]
    source = file.parent / "router.db"
    timeout = int(config["runtime"]["startup_timeout"])

    ensure_network(OXIDIZED_NETWORK)
    compose(file, project, "config", "--quiet")
    compose(file, project, "pull")
    validate_runtime(file, project)

    if not has_inventory(source):
        # Recreate on the transition back to zero nodes so a previously running
        # native Oxidized process returns to the waiting entrypoint.
        compose(file, project, "up", "-d", "--force-recreate", "oxidized")
        wait_healthy(file, project, "oxidized", timeout)
        print(
            "[OK] Oxidized container is healthy and waiting for device inventory"
        )
        return

    # With inventory present, the wrapper immediately execs the official image
    # init. If the container was already waiting, it notices router.db itself and
    # transitions without a Docker restart.
    compose(file, project, "up", "-d", "oxidized")
    wait_for_native_and_reload(file, project, timeout)
    wait_healthy(file, project, "oxidized", timeout)
    print("[OK] Oxidized ready on its private container network")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    config = yaml.safe_load(
        (ROOT / "deployment/oxidized.yaml").read_text(encoding="utf-8")
    )
    file = prepare_runtime(config)

    if args.prepare_only:
        print("[OK] Oxidized runtime prepared; no containers started")
        return

    deploy_or_reconcile(config, file)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
