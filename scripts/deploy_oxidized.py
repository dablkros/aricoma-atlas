#!/usr/bin/env python3
"""Deploy Oxidized without exposing its unauthenticated web/API port."""

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    OXIDIZED_NETWORK, compose, ensure_network, wait_healthy, write_yaml,
)


def prepare_runtime(config, root=None):
    runtime = (root or ROOT) / config["runtime"]["directory"]
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)
    settings = config["oxidized"]
    home = "/home/oxidized/.config/oxidized"
    # The named volume is writable by the official image's UID 30000.
    # Read-only deployment settings are mounted outside that volume: the image's
    # startup script recursively chowns the home volume.
    oxidized = {
        "interval": int(settings["interval"]),
        "threads": int(settings["threads"]),
        "timeout": int(settings["timeout"]),
        "retries": 3,
        "resolve_dns": False,
        "input": {"default": "ssh", "ssh": {"secure": True}},
        "output": {"default": "git", "git": {
            "user": "Atlas Oxidized", "email": "oxidized@localhost",
            "repo": f"{home}/repository.git",
        }},
        "source": {"default": "csv", "csv": {
            "file": "/etc/atlas-oxidized/router.db", "delimiter": ":",
            "map": {"name": 0, "model": 1},
        }},
        "extensions": {"oxidized-web": {
            "load": True, "listen": "0.0.0.0", "port": 8888,
        }},
    }
    # Preserve an operator-supplied source until the Atlas integration is built.
    settings_file = runtime / "config"
    if not settings_file.exists():
        write_yaml(settings_file, oxidized)
        os.chmod(settings_file, 0o644)
    source = runtime / "router.db"
    if not source.exists():
        source.touch(mode=0o644)
    file = runtime / "docker-compose.yml"
    write_yaml(file, {
        "services": {"oxidized": {
            "image": settings["docker_image"], "restart": "unless-stopped",
            "volumes": [
                "atlas_oxidized_data:/home/oxidized/.config/oxidized",
                f"{source}:/etc/atlas-oxidized/router.db:ro",
                f"{settings_file}:/etc/oxidized/config:ro",
            ],
            "networks": {"web": {"aliases": ["atlas-oxidized"]}},
            "healthcheck": {
                "test": ["CMD", "curl", "--fail", "--silent", "http://127.0.0.1:8888/nodes.json"],
                "interval": "5s", "timeout": "3s", "retries": 20,
            },
        }},
        "networks": {"web": {"external": True, "name": OXIDIZED_NETWORK}},
        "volumes": {"atlas_oxidized_data": {"name": "atlas-oxidized-data"}},
    })
    return file


def waiting_for_inventory(file):
    try:
        settings = yaml.safe_load((file.parent / "config").read_text())
    except yaml.YAMLError:
        # Operator configs can contain Ruby YAML tags; native Oxidized validates
        # those. Empty-inventory handling is for the generated bootstrap source.
        return False
    source = settings.get("source", {})
    if source.get("default") != "csv" or source.get("csv", {}).get("file") != "/etc/atlas-oxidized/router.db":
        return False
    return not any(line.strip() and not line.lstrip().startswith("#")
                   for line in (file.parent / "router.db").read_text().splitlines())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "deployment/oxidized.yaml").read_text())
    file = prepare_runtime(config)
    if args.prepare_only:
        print("[OK] Oxidized prepared; no containers started")
        return
    ensure_network(OXIDIZED_NETWORK)
    project = config["runtime"]["compose_project"]
    compose(file, project, "config", "--quiet")
    compose(file, project, "pull")
    # Native Oxidized 0.37 exits with NoNodesFound for an empty source. Do not
    # invent a production device to keep its web process alive.
    if waiting_for_inventory(file):
        compose(file, project, "stop", "oxidized")
        compose(file, project, "create", "oxidized")
        print("[OK] Oxidized prepared; collection is stopped until device inventory is supplied")
        return
    compose(file, project, "up", "-d")
    wait_healthy(file, project, "oxidized", config["runtime"]["startup_timeout"])
    print("[OK] Oxidized ready on its private container network")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
