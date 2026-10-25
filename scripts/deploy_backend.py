#!/usr/bin/env python3
"""Build and deploy the containerized Atlas FastAPI backend."""

import argparse
import copy
import ipaddress
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas import __version__  # noqa: E402
from atlas.components import load_components  # noqa: E402
from atlas.openbao_client import (  # noqa: E402
    OpenBaoClient,
    OpenBaoError,
    OpenBaoNotFound,
)
from atlas.deployment import (  # noqa: E402
    NETBOX_NETWORK,
    OPENBAO_NETWORK,
    OXIDIZED_NETWORK,
    ZABBIX_NETWORK,
    compose,
    wait_healthy,
    write_yaml,
)
from atlas.proxy import (  # noqa: E402
    load_config as load_proxy_config,
    public_service_urls,
)
from atlas.monitoring.models import (  # noqa: E402
    MonitoringError,
    load_config as load_monitoring_config,
)


CONFIG_FILE = ROOT / "deployment/backend.yaml"
MONITORING_DEFAULT_FILE = ROOT / "deployment/monitoring.default.yaml"
IMAGE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*:[A-Za-z0-9][A-Za-z0-9._-]*")
PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
OPENBAO_URL = "http://127.0.0.1:18200"
BACKEND_SECRET_PATH = "backend"
OPERATOR_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,256}")


def _route_probe_address(network):
    network = ipaddress.ip_network(network)
    if network.num_addresses > 2:
        return str(network.network_address + 1)
    return str(network.network_address)


def host_route_source(target, runner=subprocess.run):
    try:
        result = runner(
            ["ip", "route", "get", target],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("Unable to execute ip route get for monitoring egress validation") from exc
    if result.returncode != 0:
        raise RuntimeError(f"No host route is available for monitoring target {target}")
    match = re.search(r"\bsrc\s+(\S+)", result.stdout)
    if not match:
        raise RuntimeError(f"Host route for monitoring target {target} has no source address")
    try:
        source = ipaddress.ip_address(match.group(1))
    except ValueError as exc:
        raise RuntimeError(f"Host route for monitoring target {target} has an invalid source") from exc
    if source.version != 4:
        raise RuntimeError(f"Host route for monitoring target {target} is not IPv4")
    return str(source)


def validate_monitoring_egress(path, route_source=host_route_source):
    try:
        config = load_monitoring_config(path)
    except MonitoringError as exc:
        raise RuntimeError(f"Invalid monitoring configuration: {exc.code}") from None
    monitoring = config["monitoring"]
    profiles = monitoring.get("egress_profiles")
    checks = []
    if profiles:
        for name, profile in profiles.items():
            if profile.get("proxy_name"):
                continue
            checks.extend(
                (name, network, profile["source_ip"])
                for network in profile["device_networks"]
            )
    elif monitoring.get("source_ip"):
        checks.extend(
            ("legacy-direct", network, monitoring["source_ip"])
            for network in config["allowed_device_networks"]
        )
    for name, network, expected in checks:
        target = _route_probe_address(network)
        actual = route_source(target)
        if actual != expected:
            raise RuntimeError(
                "Monitoring egress profile "
                f"{name} expects source {expected} for {network}, but the host route "
                f"to {target} selects {actual}"
            )
    return config


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

    if not isinstance(backend.get("fortios_validate_certs"), bool):
        raise ValueError("backend.fortios_validate_certs must be a boolean")

    job_timeout = backend.get("ansible_job_timeout")
    if (
        isinstance(job_timeout, bool)
        or not isinstance(job_timeout, int)
        or not 1 <= job_timeout <= 1800
    ):
        raise ValueError(
            "backend.ansible_job_timeout must be an integer from 1 to 1800"
        )

    retention = backend.get("prophylaxis_result_retention")
    if (
        isinstance(retention, bool)
        or not isinstance(retention, int)
        or not 100 <= retention <= 1_000_000
    ):
        raise ValueError(
            "backend.prophylaxis_result_retention must be an integer from 100 to 1000000"
        )

    concurrency = backend.get("prophylaxis_concurrency")
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or not 1 <= concurrency <= 32
    ):
        raise ValueError(
            "backend.prophylaxis_concurrency must be an integer from 1 to 32"
        )

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


def _validate_operator_token(value):
    if not isinstance(value, str) or not OPERATOR_TOKEN_PATTERN.fullmatch(value):
        raise RuntimeError("Atlas operator token has an invalid format")
    return value


def _read_local_operator_token(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Monitoring operator token is not a regular file")
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("Unable to read the monitoring operator token") from exc
    if value.endswith("\n"):
        value = value[:-1]
    return _validate_operator_token(value)


def _materialize_operator_token(path, token):
    token = _validate_operator_token(token)
    if path.exists() and path.is_symlink():
        raise RuntimeError("Monitoring operator token cannot be a symlink")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def sync_backend_operator_token(root=ROOT, client=None):
    """Reconcile the shared Atlas operator token to its runtime file."""
    root = Path(root).resolve()
    runtime_base = root / ".runtime"
    if runtime_base.is_symlink():
        raise RuntimeError(f"Atlas runtime cannot be a symlink: {runtime_base}")
    monitoring_directory = runtime_base / "monitoring"
    if monitoring_directory.is_symlink():
        raise RuntimeError("Monitoring runtime cannot be a symlink")
    if runtime_base.resolve() not in monitoring_directory.resolve().parents:
        raise RuntimeError("Monitoring runtime escaped the protected runtime directory")
    monitoring_directory.mkdir(parents=True, exist_ok=True)
    os.chmod(monitoring_directory, 0o700)
    token_file = monitoring_directory / "operator-token"
    identity_file = root / ".runtime/openbao-approle.json"
    client = client or OpenBaoClient(
        os.environ.get("OPENBAO_URL", OPENBAO_URL),
        timeout=15,
    )

    try:
        openbao_token = client.login_from_identity(identity_file)
    except OpenBaoError as exc:
        raise RuntimeError(
            "Atlas deployer cannot authenticate to OpenBao while reconciling "
            "the Atlas operator token"
        ) from exc

    try:
        try:
            backend_secret, metadata = client.kv_read_with_metadata(
                openbao_token,
                BACKEND_SECRET_PATH,
                mount="atlas",
            )
        except OpenBaoNotFound:
            backend_secret = None
            metadata = None
        except OpenBaoError as exc:
            raise RuntimeError(
                "Unable to read the Atlas backend secret from OpenBao"
            ) from exc

        if backend_secret is not None and "operator_token" in backend_secret:
            operator_token = _validate_operator_token(
                backend_secret.get("operator_token")
            )
        else:
            if token_file.exists() or token_file.is_symlink():
                operator_token = _read_local_operator_token(token_file)
            else:
                operator_token = secrets.token_urlsafe(32)

            if backend_secret is None:
                updated_secret = {"operator_token": operator_token}
                cas = 0
            else:
                if not isinstance(metadata, dict):
                    raise RuntimeError("OpenBao backend secret metadata is invalid")
                version = metadata.get("version")
                if (
                    isinstance(version, bool)
                    or not isinstance(version, int)
                    or version < 1
                ):
                    raise RuntimeError("OpenBao backend secret version is invalid")
                updated_secret = dict(backend_secret)
                updated_secret["operator_token"] = operator_token
                cas = version

            try:
                client.kv_write(
                    openbao_token,
                    BACKEND_SECRET_PATH,
                    updated_secret,
                    mount="atlas",
                    cas=cas,
                )
            except OpenBaoError as exc:
                raise RuntimeError(
                    "Unable to store the Atlas operator token in OpenBao"
                ) from exc

        _materialize_operator_token(token_file, operator_token)
    finally:
        try:
            client.revoke_self(openbao_token)
        except OpenBaoError as exc:
            raise RuntimeError(
                "Atlas operator token was reconciled, but the OpenBao "
                "deployment token could not be revoked"
            ) from exc

    return token_file


def prepare_runtime(config, root=ROOT, ui_urls=None, selection=None):
    root = Path(root).resolve()
    backend = config["backend"]
    selection = selection or load_components(root / ".runtime/components.yaml")
    required_ui_urls = set(selection.ingress_services)
    if not isinstance(ui_urls, dict) or set(ui_urls) != required_ui_urls:
        raise RuntimeError(
            "Atlas backend UI URLs do not match the selected components"
        )
    if any(
        not isinstance(value, str) or not value.startswith("https://")
        for value in ui_urls.values()
    ):
        raise RuntimeError("Atlas backend UI URLs must use HTTPS")
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

    monitoring_identity_file = root / ".runtime/openbao-monitoring.json"
    if monitoring_identity_file.is_symlink() or not monitoring_identity_file.is_file():
        raise RuntimeError(
            "Atlas monitoring provisioner AppRole identity is missing or is not a regular file"
        )

    monitoring_directory = root / ".runtime/monitoring"
    if monitoring_directory.is_symlink():
        raise RuntimeError("Monitoring runtime cannot be a symlink")
    if runtime_base.resolve() not in monitoring_directory.resolve().parents:
        raise RuntimeError("Monitoring runtime escaped the protected runtime directory")
    monitoring_directory.mkdir(parents=True, exist_ok=True)
    os.chmod(monitoring_directory, 0o700)
    monitoring_jobs = monitoring_directory / "jobs"
    if monitoring_jobs.is_symlink():
        raise RuntimeError("Monitoring jobs directory cannot be a symlink")
    monitoring_jobs.mkdir(parents=True, exist_ok=True)
    os.chmod(monitoring_jobs, 0o700)
    monitoring_trust = monitoring_directory / "trust"
    if monitoring_trust.is_symlink():
        raise RuntimeError("Monitoring trust directory cannot be a symlink")
    monitoring_trust.mkdir(parents=True, exist_ok=True)
    os.chmod(monitoring_trust, 0o700)

    monitoring_config = monitoring_directory / "config.yaml"
    if not monitoring_config.exists():
        if not MONITORING_DEFAULT_FILE.is_file():
            raise RuntimeError("Safe monitoring default configuration is missing")
        monitoring_config.write_text(
            MONITORING_DEFAULT_FILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    if monitoring_config.is_symlink() or not monitoring_config.is_file():
        raise RuntimeError("Monitoring configuration is not a regular file")
    os.chmod(monitoring_config, 0o600)
    validate_monitoring_egress(monitoring_config)

    monitoring_operator_token = monitoring_directory / "operator-token"
    if monitoring_operator_token.is_symlink() or not monitoring_operator_token.is_file():
        raise RuntimeError(
            "Monitoring operator token is missing; reconcile it from OpenBao first"
        )
    _read_local_operator_token(monitoring_operator_token)
    os.chmod(monitoring_operator_token, 0o600)

    known_hosts_file = root / ".runtime/oxidized/ssh/known_hosts"
    if (
        backend["ssh_strict_host_keys"]
        and (known_hosts_file.is_symlink() or not known_hosts_file.is_file())
    ):
        raise RuntimeError("Shared SSH known_hosts is not prepared")

    inventory_directory = Path(
        os.environ.get("ATLAS_OXIDIZED_RUN_DIR", "/run/atlas/oxidized")
    )
    if selection.enabled("oxidized"):
        if not inventory_directory.is_absolute():
            raise RuntimeError("ATLAS_OXIDIZED_RUN_DIR must be an absolute path")
        inventory_file = inventory_directory / "router.json"
        if inventory_directory.is_symlink() or not inventory_file.is_file():
            raise RuntimeError("Oxidized runtime inventory is not prepared")

    prophylaxis_directory = root / ".runtime/prophylaxis"
    if prophylaxis_directory.is_symlink():
        raise RuntimeError("Profylaxia runtime cannot be a symlink")
    if runtime_base.resolve() not in prophylaxis_directory.resolve().parents:
        raise RuntimeError("Profylaxia runtime escaped the protected runtime directory")
    prophylaxis_directory.mkdir(parents=True, exist_ok=True)
    os.chmod(prophylaxis_directory, 0o700)

    inventory_config = root / ".runtime/inventory.yaml"
    inventory_device_types = root / ".runtime/inventory/device-types"
    inventory_device_types.mkdir(parents=True, exist_ok=True)
    os.chmod(inventory_device_types, 0o750)

    built_catalog = root / "build"
    built_device_types = built_catalog / "device-types"
    catalog_report = built_catalog / "catalog-report.json"
    upstream_device_types = root / "devicetype-library/device-types"
    if (
        built_catalog.is_symlink()
        or built_device_types.is_symlink()
        or not built_device_types.is_dir()
    ):
        raise RuntimeError("Built Device Type catalog is missing or invalid")
    if catalog_report.is_symlink() or not catalog_report.is_file():
        raise RuntimeError("Device Type catalog report is missing")
    if upstream_device_types.is_symlink() or not upstream_device_types.is_dir():
        raise RuntimeError("Pinned upstream Device Type catalog is missing or invalid")

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
        f"{monitoring_identity_file}:/run/secrets/atlas-monitoring.json:ro",
        f"{monitoring_config}:/run/secrets/atlas-monitoring-config.yaml:ro",
        f"{monitoring_operator_token}:/run/secrets/atlas-monitoring-operator-token:ro",
        f"{monitoring_jobs}:/run/atlas/monitoring/jobs:rw",
        f"{monitoring_trust}:/run/atlas/monitoring/trust:ro",
        f"{prophylaxis_directory}:/run/atlas/prophylaxis:rw",
        f"{inventory_device_types}:/run/atlas/inventory/device-types:rw",
        f"{built_catalog}:/app/build:ro",
        f"{upstream_device_types}:/app/devicetype-library/device-types:ro",
    ]
    if inventory_config.is_file():
        volumes.append(f"{inventory_config}:/run/atlas/inventory/config.yaml:ro")
    if selection.enabled("oxidized"):
        volumes.append(f"{inventory_directory}:/run/atlas/oxidized:rw")
    if backend["ssh_strict_host_keys"]:
        volumes.append(
            f"{known_hosts_file}:/run/atlas/ssh/known_hosts:ro"
        )

    environment = {
        "ATLAS_APP_VERSION": backend["version"],
        "ATLAS_ENVIRONMENT": backend["environment"],
        "ATLAS_LOG_LEVEL": backend["log_level"],
        "ATLAS_OPENBAO_URL": "http://atlas-openbao:8200",
        "ATLAS_OPENBAO_IDENTITY_FILE": "/run/secrets/atlas-backend.json",
        "ATLAS_MONITORING_IDENTITY_FILE": "/run/secrets/atlas-monitoring.json",
        "ATLAS_MONITORING_CONFIG_FILE": "/run/secrets/atlas-monitoring-config.yaml",
        "ATLAS_MONITORING_OPERATOR_TOKEN_FILE": "/run/secrets/atlas-monitoring-operator-token",
        "ATLAS_MONITORING_JOBS_DIRECTORY": "/run/atlas/monitoring/jobs",
        "ATLAS_NETBOX_URL": "http://atlas-netbox:8080",
        "ATLAS_NETBOX_UI_URL": ui_urls["netbox"],
        "ATLAS_INVENTORY_CONFIG": "/run/atlas/inventory/config.yaml",
        "ATLAS_INVENTORY_DEVICE_TYPES": "/run/atlas/inventory/device-types",
        "ATLAS_OXIDIZED_ENABLED": str(selection.enabled("oxidized")).lower(),
        "ATLAS_ZABBIX_ENABLED": str(selection.enabled("zabbix")).lower(),
        "ATLAS_PROPHYLAXIS_RESULTS_FILE": "/run/atlas/prophylaxis/results.sqlite3",
        "ATLAS_PROPHYLAXIS_RUNS_DIRECTORY": "/run/atlas/prophylaxis/runs",
        "ATLAS_PROPHYLAXIS_RESULT_RETENTION": str(backend["prophylaxis_result_retention"]),
        "ATLAS_PROPHYLAXIS_CONCURRENCY": str(backend["prophylaxis_concurrency"]),
        "ATLAS_ANSIBLE_PROJECT_DIR": "/app/automation",
        "ATLAS_ANSIBLE_JOB_TIMEOUT": str(backend["ansible_job_timeout"]),
        "ATLAS_SSH_KNOWN_HOSTS_FILE": "/run/atlas/ssh/known_hosts",
        "ATLAS_SSH_STRICT_HOST_KEYS": str(backend["ssh_strict_host_keys"]).lower(),
        "ATLAS_FORTIOS_VALIDATE_CERTS": str(backend["fortios_validate_certs"]).lower(),
    }
    if selection.enabled("oxidized"):
        environment.update({
            "ATLAS_OXIDIZED_URL": "http://atlas-oxidized:8888",
            "ATLAS_OXIDIZED_UI_URL": ui_urls["oxidized"],
            "ATLAS_OXIDIZED_INVENTORY_FILE": "/run/atlas/oxidized/router.json",
        })
    if selection.enabled("zabbix"):
        environment.update({
            "ATLAS_ZABBIX_URL": "http://atlas-zabbix-web:8080/api_jsonrpc.php",
            "ATLAS_ZABBIX_UI_URL": ui_urls["zabbix"],
        })
    service_networks = {"openbao": {}, "netbox": {}}
    external_networks = {
        "openbao": {"external": True, "name": OPENBAO_NETWORK},
        "netbox": {"external": True, "name": NETBOX_NETWORK},
    }
    if selection.enabled("oxidized"):
        service_networks["oxidized"] = {}
        external_networks["oxidized"] = {"external": True, "name": OXIDIZED_NETWORK}
    if selection.enabled("zabbix"):
        service_networks["zabbix"] = {}
        external_networks["zabbix"] = {"external": True, "name": ZABBIX_NETWORK}

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
                    "environment": environment,
                    "user": f"{runtime_user}:{runtime_group}",
                    "volumes": volumes,
                    "networks": service_networks,
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
            "networks": external_networks,
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
    proxy_config = load_proxy_config()
    sync_backend_operator_token()
    selection = load_components()
    compose_file = prepare_runtime(
        config,
        ui_urls=public_service_urls(proxy_config),
        selection=selection,
    )

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
