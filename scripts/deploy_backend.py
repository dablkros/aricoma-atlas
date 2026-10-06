#!/usr/bin/env python3
"""Build and deploy the containerized Atlas FastAPI backend."""

import argparse
import copy
import ipaddress
import os
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas import __version__  # noqa: E402
from atlas.deployment import (  # noqa: E402
    NETBOX_NETWORK,
    OPENBAO_NETWORK,
    OXIDIZED_NETWORK,
    compose,
    wait_healthy,
    write_yaml,
)


CONFIG_FILE = ROOT / "deployment/backend.yaml"
IMAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*:[A-Za-z0-9][A-Za-z0-9._-]*")
PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def load_config(path=CONFIG_FILE):
    try:
        config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read Atlas backend configuration: {path}") from exc
    return validate_config(config)


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("Atlas backend configuration must be an object")

    config = copy.deepcopy(raw)
    try:
        backend = config["backend"]
        runtime = config["runtime"]
        network = config["network"]
    except KeyError as exc:
        raise ValueError(f"Missing Atlas backend configuration section: {exc.args[0]}") from None

    if not all(isinstance(section, dict) for section in (backend, runtime, network)):
        raise ValueError("Atlas backend configuration sections must be objects")

    for field in ("version", "docker_image", "python_base_image", "dockerfile"):
        if not isinstance(backend.get(field), str) or not backend[field].strip():
            raise ValueError(f"backend.{field} must be a non-empty string")

    if backend["version"] != __version__:
        raise ValueError(
            "backend.version must match the Atlas package version "
            f"({__version__})"
        )

    for field in ("docker_image", "python_base_image"):
        if not IMAGE_PATTERN.fullmatch(backend[field]):
            raise ValueError(f"backend.{field} must use an explicit version tag")

    if backend.get("environment") not in {"development", "test", "production"}:
        raise ValueError("backend.environment is invalid")

    log_level = backend.get("log_level")
    if not isinstance(log_level, str) or log_level.upper() not in LOG_LEVELS:
        raise ValueError("backend.log_level is invalid")
    backend["log_level"] = log_level.upper()

    if not isinstance(backend.get("ssh_strict_host_keys"), bool):
        raise ValueError("backend.ssh_strict_host_keys must be a boolean")

    dockerfile = Path(backend["dockerfile"])
    if dockerfile.is_absolute() or ".." in dockerfile.parts:
        raise ValueError("backend.dockerfile must be a safe repository-relative path")

    runtime_directory = Path(str(runtime.get("directory", "")))
    if (
        runtime_directory.is_absolute()
        or ".." in runtime_directory.parts
        or len(runtime_directory.parts) < 2
        or runtime_directory.parts[0] != ".runtime"
    ):
        raise ValueError("runtime.directory must be below .runtime/")

    project = runtime.get("compose_project")
    if not isinstance(project, str) or not PROJECT_PATTERN.fullmatch(project):
        raise ValueError("runtime.compose_project is invalid")

    timeout = runtime.get("startup_timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise ValueError("runtime.startup_timeout must be a positive integer")

    try:
        address = ipaddress.ip_address(network["listen_address"])
    except (KeyError, ValueError) as exc:
        raise ValueError("network.listen_address must be a valid IP address") from exc
    if not address.is_loopback:
        raise ValueError("Atlas backend may only publish on a loopback address")
    network["listen_address"] = str(address)

    for field in ("host_port", "container_port"):
        port = network.get(field)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"network.{field} must be an integer from 1 to 65535")

    return config


def runtime_path(config, root=ROOT):
    return Path(root) / config["runtime"]["directory"]


def prepare_runtime(config, root=ROOT):
    root = Path(root).resolve()
    backend = config["backend"]
    runtime_base = root / ".runtime"
    if runtime_base.is_symlink():
        raise RuntimeError(f"Atlas runtime cannot be a symlink: {runtime_base}")
    runtime = runtime_path(config, root)
    if runtime.is_symlink():
        raise RuntimeError(f"Atlas backend runtime cannot be a symlink: {runtime}")
    if runtime_base.resolve() not in runtime.resolve().parents:
        raise RuntimeError("Atlas backend runtime escaped the protected runtime directory")
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)

    dockerfile = (root / config["backend"]["dockerfile"]).resolve()
    if root not in dockerfile.parents or not dockerfile.is_file():
        raise RuntimeError(f"Atlas backend Dockerfile not found: {dockerfile}")

    identity_file = root / ".runtime/openbao-backend.json"
    if identity_file.is_symlink() or not identity_file.is_file():
        raise RuntimeError(
            "Atlas backend AppRole identity is missing or is not a regular file"
        )

    known_hosts_file = root / ".runtime/oxidized/ssh/known_hosts"
    if (
        backend["ssh_strict_host_keys"]
        and (known_hosts_file.is_symlink() or not known_hosts_file.is_file())
    ):
        raise RuntimeError("Shared SSH known_hosts is not prepared")

    inventory_directory = Path(
        os.environ.get("ATLAS_OXIDIZED_RUN_DIR", "/run/atlas/oxidized")
    )
    if not inventory_directory.is_absolute():
        raise RuntimeError("ATLAS_OXIDIZED_RUN_DIR must be an absolute path")
    inventory_file = inventory_directory / "router.json"
    if inventory_directory.is_symlink() or not inventory_file.is_file():
        raise RuntimeError("Oxidized runtime inventory is not prepared")

    runtime_user = os.geteuid()
    runtime_group = os.getegid()
    if runtime_user == 0:
        raise RuntimeError(
            "Atlas backend deployment must run as the Atlas service user"
        )

    network = config["network"]
    healthcheck = (
        "import json,urllib.request; "
        f"response=urllib.request.urlopen('http://127.0.0.1:{network['container_port']}/api/health', timeout=2); "
        "data=json.load(response); "
        "assert response.status == 200 and data.get('status') == 'ok'"
    )
    volumes = [
        f"{identity_file}:/run/secrets/atlas-backend.json:ro",
        f"{inventory_directory}:/run/atlas/oxidized:rw",
    ]
    if backend["ssh_strict_host_keys"]:
        volumes.append(
            f"{known_hosts_file}:/run/atlas/ssh/known_hosts:ro"
        )

    compose_file = runtime / "docker-compose.yml"
    write_yaml(
        compose_file,
        {
            "services": {
                "backend": {
                    "image": backend["docker_image"],
                    "build": {
                        "context": str(root),
                        "dockerfile": backend["dockerfile"],
                        "args": {
                            "ATLAS_VERSION": backend["version"],
                            "PYTHON_BASE_IMAGE": backend["python_base_image"],
                        },
                    },
                    "restart": "unless-stopped",
                    "init": True,
                    "environment": {
                        "ATLAS_APP_VERSION": backend["version"],
                        "ATLAS_ENVIRONMENT": backend["environment"],
                        "ATLAS_LOG_LEVEL": backend["log_level"],
                        "ATLAS_OPENBAO_URL": "http://atlas-openbao:8200",
                        "ATLAS_OPENBAO_IDENTITY_FILE": "/run/secrets/atlas-backend.json",
                        "ATLAS_NETBOX_URL": "http://atlas-netbox:8080",
                        "ATLAS_OXIDIZED_URL": "http://atlas-oxidized:8888",
                        "ATLAS_OXIDIZED_INVENTORY_FILE": "/run/atlas/oxidized/router.json",
                        "ATLAS_SSH_KNOWN_HOSTS_FILE": "/run/atlas/ssh/known_hosts",
                        "ATLAS_SSH_STRICT_HOST_KEYS": str(
                            backend["ssh_strict_host_keys"]
                        ).lower(),
                    },
                    "user": f"{runtime_user}:{runtime_group}",
                    "volumes": volumes,
                    "networks": {
                        "openbao": {},
                        "netbox": {},
                        "oxidized": {},
                    },
                    "ports": [
                        f"{network['listen_address']}:{network['host_port']}:{network['container_port']}"
                    ],
                    "read_only": True,
                    "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777"],
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "pids_limit": 256,
                    "healthcheck": {
                        "test": ["CMD", "python", "-c", healthcheck],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 20,
                        "start_period": "10s",
                    },
                }
            },
            "networks": {
                "openbao": {"external": True, "name": OPENBAO_NETWORK},
                "netbox": {"external": True, "name": NETBOX_NETWORK},
                "oxidized": {"external": True, "name": OXIDIZED_NETWORK},
            },
        },
    )
    os.chmod(compose_file, 0o600)
    return compose_file


def deploy_runtime(config, compose_file):
    project = config["runtime"]["compose_project"]
    compose(compose_file, project, "config", "--quiet")
    compose(compose_file, project, "build", "--pull", "backend")
    compose(compose_file, project, "up", "-d", "--force-recreate", "backend")
    wait_healthy(
        compose_file,
        project,
        "backend",
        config["runtime"]["startup_timeout"],
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate and validate the backend runtime without building or starting it",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    compose_file = prepare_runtime(config)

    if args.prepare_only:
        print("[OK] Atlas backend runtime prepared; image not built and container not started")
        return

    deploy_runtime(config, compose_file)
    network = config["network"]
    print(
        "[OK] Atlas backend healthy: "
        f"http://{network['listen_address']}:{network['host_port']}/api/health"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
