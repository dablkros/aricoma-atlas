#!/usr/bin/env python3

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import yaml
import requests


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from atlas.openbao_client import (  # noqa: E402
    OpenBaoClient,
    OpenBaoError,
    OpenBaoNotFound,
)
from atlas.deployment import NETBOX_NETWORK, ensure_network, write_private  # noqa: E402
from atlas.proxy import load_config as load_proxy_config  # noqa: E402


CONFIG_FILE = (
    ROOT
    / "deployment"
    / "netbox.yaml"
)

OPENBAO_IDENTITY_FILE = (
    ROOT
    / ".runtime"
    / "openbao-approle.json"
)

OPENBAO_URL = os.environ.get(
    "OPENBAO_URL",
    "http://127.0.0.1:18200",
)

COMPOSE_SECRET_ENV = {}


# ===========================================================================
# CLI
# ===========================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Deploy the Atlas NetBox "
            "Docker environment"
        )
    )

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help=(
            "Prepare runtime repository, "
            "secrets and Docker Compose "
            "configuration without starting "
            "containers"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Show full output from the "
            "Atlas NetBox bootstrap"
        ),
    )

    return parser.parse_args()


# ===========================================================================
# Output
# ===========================================================================

def header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def info(message):
    print(
        f"[INFO] {message}"
    )


def ok(message):
    print(
        f"[OK] {message}"
    )


# ===========================================================================
# Commands
# ===========================================================================

def run(
    command,
    cwd=None,
    capture=False,
    check=True,
    env=None,
):
    if capture:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            env=env,
        )

    else:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
        )

    if (
        check
        and
        result.returncode != 0
    ):
        if capture:
            if result.stdout:
                print(
                    result.stdout
                )

            if result.stderr:
                print(
                    result.stderr,
                    file=sys.stderr,
                )

        raise RuntimeError(
            "Command failed: "
            + " ".join(command)
        )

    return result


def command_output(
    command,
    cwd=None,
):
    result = run(
        command,
        cwd=cwd,
        capture=True,
    )

    return result.stdout.strip()


# ===========================================================================
# Config
# ===========================================================================

def load_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            f"Deployment config not found: "
            f"{CONFIG_FILE}"
        )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:

        config = yaml.safe_load(
            handle
        )

    if not isinstance(
        config,
        dict,
    ):
        raise RuntimeError(
            "deployment/netbox.yaml "
            "has invalid YAML root"
        )

    required = [
        (
            "netbox",
            "version",
        ),
        (
            "netbox",
            "docker_image",
        ),
        (
            "netbox_docker",
            "repository",
        ),
        (
            "netbox_docker",
            "ref",
        ),
        (
            "runtime",
            "directory",
        ),
        (
            "runtime",
            "compose_project",
        ),
        (
            "runtime",
            "startup_timeout",
        ),
        (
            "runtime",
            "poll_interval",
        ),
        (
            "network",
            "listen_address",
        ),
        (
            "network",
            "port",
        ),
        (
            "bootstrap",
            "superuser_name",
        ),
    ]

    for section, key in required:
        if (
            section not in config
            or
            key not in config[section]
        ):
            raise RuntimeError(
                f"Missing configuration: "
                f"{section}.{key}"
            )

    try:
        startup_timeout = int(
            config[
                "runtime"
            ][
                "startup_timeout"
            ]
        )

        poll_interval = int(
            config[
                "runtime"
            ][
                "poll_interval"
            ]
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "runtime.startup_timeout and "
            "runtime.poll_interval must "
            "be integers"
        ) from exc

    if startup_timeout < 30:
        raise RuntimeError(
            "runtime.startup_timeout "
            "must be at least 30 seconds"
        )

    if poll_interval < 1:
        raise RuntimeError(
            "runtime.poll_interval "
            "must be at least 1 second"
        )

    repository = config[
        "netbox_docker"
    ][
        "repository"
    ]

    if (
        "[" in repository
        or
        "]" in repository
        or
        "(" in repository
        or
        ")" in repository
    ):
        raise RuntimeError(
            "netbox_docker.repository "
            "looks like a Markdown link. "
            "Use a plain Git URL."
        )

    return config


# ===========================================================================
# Docker checks
# ===========================================================================

def check_docker():
    header(
        "DOCKER PREFLIGHT"
    )

    try:
        docker_version = (
            command_output(
                [
                    "docker",
                    "--version",
                ]
            )
        )

    except Exception:
        raise RuntimeError(
            "Docker is not installed "
            "or is not available in PATH"
        )

    ok(
        docker_version
    )

    try:
        compose_version = (
            command_output(
                [
                    "docker",
                    "compose",
                    "version",
                ]
            )
        )

    except Exception:
        raise RuntimeError(
            "Docker Compose plugin "
            "is not available"
        )

    ok(
        compose_version
    )

    result = run(
        [
            "docker",
            "info",
        ],
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Docker daemon is not running "
            "or current user cannot access it"
        )

    ok(
        "Docker daemon reachable"
    )


# ===========================================================================
# Runtime repository
# ===========================================================================

def normalize_git_url(value):
    value = (
        value.strip()
        .rstrip("/")
    )

    if value.endswith(".git"):
        value = value[:-4]

    return value


def prepare_configuration_permissions(runtime_dir):
    """Make the non-secret bind mount readable by the container's NetBox user.

    Git checkout inherits the caller's umask. Keep env files, AppRole identities
    and the enclosing runtime private; only this mounted configuration is shared.
    """
    directory = runtime_dir / "configuration"
    paths = [directory, *directory.rglob("*")]
    if any(path.is_symlink() for path in paths):
        raise RuntimeError("NetBox configuration must not contain symbolic links")
    for path in paths:
        path.chmod(0o755 if path.is_dir() else 0o644)


def prepare_netbox_docker(
    config,
):
    header(
        "NETBOX DOCKER RUNTIME"
    )

    repository = config[
        "netbox_docker"
    ][
        "repository"
    ]

    ref = config[
        "netbox_docker"
    ][
        "ref"
    ]

    runtime_dir = (
        ROOT
        / config[
            "runtime"
        ][
            "directory"
        ]
    ).resolve()

    runtime_dir.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------------------------------------
    # Clone
    # -----------------------------------------------------------------------

    if not runtime_dir.exists():
        info(
            "Cloning NetBox Docker"
        )

        run(
            [
                "git",
                "clone",
                repository,
                str(runtime_dir),
            ]
        )

    if not (
        runtime_dir
        / ".git"
    ).exists():
        raise RuntimeError(
            f"{runtime_dir} exists but "
            f"is not a Git repository"
        )

    # -----------------------------------------------------------------------
    # Protect tracked local changes
    # -----------------------------------------------------------------------

    dirty = command_output(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        cwd=runtime_dir,
    )

    if dirty:
        raise RuntimeError(
            "NetBox Docker runtime contains "
            "modified tracked files. "
            "Refusing to overwrite them."
        )

    # -----------------------------------------------------------------------
    # Correct origin
    # -----------------------------------------------------------------------

    current_origin = (
        command_output(
            [
                "git",
                "remote",
                "get-url",
                "origin",
            ],
            cwd=runtime_dir,
        )
    )

    if (
        normalize_git_url(
            current_origin
        )
        !=
        normalize_git_url(
            repository
        )
    ):
        info(
            "Updating NetBox Docker "
            "origin repository"
        )

        run(
            [
                "git",
                "remote",
                "set-url",
                "origin",
                repository,
            ],
            cwd=runtime_dir,
        )

    # -----------------------------------------------------------------------
    # Fetch and checkout
    # -----------------------------------------------------------------------

    info(
        "Fetching NetBox Docker refs"
    )

    run(
        [
            "git",
            "fetch",
            "origin",
            "--tags",
            "--prune",
        ],
        cwd=runtime_dir,
    )

    info(
        f"Checking out pinned ref: "
        f"{ref}"
    )

    run(
        [
            "git",
            "checkout",
            "--detach",
            ref,
        ],
        cwd=runtime_dir,
    )

    commit = command_output(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=runtime_dir,
    )

    ok(
        f"NetBox Docker ref {ref}"
    )

    ok(
        f"NetBox Docker commit {commit}"
    )

    return runtime_dir


def ensure_netbox_configuration_permissions(
    runtime_dir,
):
    """Normalize permissions required by the upstream NetBox bind mount.

    NetBox Docker bind-mounts ./configuration to /etc/netbox/config and runs
    the application as the image's non-root ``netbox`` user. Git does not
    preserve directory permissions, so a restrictive host umask (for example
    077) can produce configuration directories/files that the container cannot
    traverse or read.

    Only upstream Git-tracked files under configuration/ are normalized here.
    Atlas-generated files use explicit modes when they are written.
    """
    configuration_dir = (
        runtime_dir
        / "configuration"
    )

    if not configuration_dir.is_dir():
        raise RuntimeError(
            "NetBox Docker configuration directory "
            f"not found: {configuration_dir}"
        )

    # The bind-mount root must be traversable by the NetBox container user.
    os.chmod(
        configuration_dir,
        0o755,
    )

    tracked = command_output(
        [
            "git",
            "ls-files",
            "--",
            "configuration",
        ],
        cwd=runtime_dir,
    )

    normalized_files = 0

    for relative in tracked.splitlines():
        relative_path = Path(relative)

        if (
            not relative_path.parts
            or relative_path.parts[0] != "configuration"
            or ".." in relative_path.parts
        ):
            raise RuntimeError(
                "Unexpected tracked NetBox configuration path: "
                f"{relative}"
            )

        path = runtime_dir / relative_path

        # Do not follow repository symlinks while changing host permissions.
        if path.is_symlink():
            continue

        if not path.is_file():
            continue

        parent = path.parent

        while True:
            os.chmod(
                parent,
                0o755,
            )

            if parent == configuration_dir:
                break

            if configuration_dir not in parent.parents:
                raise RuntimeError(
                    "Tracked NetBox configuration file escaped "
                    "the configuration directory"
                )

            parent = parent.parent

        # Upstream configuration files contain application configuration, not
        # Atlas secrets. Atlas runtime secrets stay in OpenBao/environment.
        os.chmod(
            path,
            0o644,
        )
        normalized_files += 1

    ok(
        "NetBox configuration bind-mount permissions ready "
        f"({normalized_files} tracked file(s))"
    )


# ===========================================================================
# OpenBao-backed NetBox secrets
# ===========================================================================

def generate_secret(
    length=32,
):
    return secrets.token_urlsafe(
        length
    )


def connect_openbao():
    header(
        "OPENBAO SECRETS"
    )

    client = OpenBaoClient(
        OPENBAO_URL
    )

    try:
        status = client.seal_status()
    except OpenBaoError as exc:
        raise RuntimeError(
            "Unable to contact OpenBao: "
            f"{exc}"
        ) from exc

    if not status.get(
        "initialized"
    ):
        raise RuntimeError(
            "OpenBao is not initialized"
        )

    if status.get(
        "sealed"
    ):
        raise RuntimeError(
            "OpenBao is sealed. Run the "
            "OpenBao deploy/unseal flow first."
        )

    if not OPENBAO_IDENTITY_FILE.exists():
        raise RuntimeError(
            "OpenBao Atlas deployer identity "
            "not found: "
            f"{OPENBAO_IDENTITY_FILE}"
        )

    try:
        token = client.login_from_identity(
            OPENBAO_IDENTITY_FILE
        )
    except OpenBaoError as exc:
        raise RuntimeError(
            "Unable to authenticate to OpenBao "
            "using the Atlas deployer identity: "
            f"{exc}"
        ) from exc

    ok(
        "OpenBao atlas-deployer "
        "authentication successful"
    )

    return (
        client,
        token,
    )


def openbao_read_optional(
    client,
    token,
    path,
):
    try:
        return client.kv_read(
            token,
            path,
        )

    except OpenBaoNotFound:
        return None
    except OpenBaoError as exc:
        raise RuntimeError(
            f"Unable to read OpenBao secret atlas/{path}"
        ) from exc


def ensure_secret_path(
    client,
    token,
    path,
    defaults,
):
    data = openbao_read_optional(
        client,
        token,
        path,
    )

    created = data is None

    if data is None:
        data = {}

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            f"OpenBao secret atlas/{path} "
            "does not contain an object"
        )

    changed = False

    for key, default in defaults.items():
        if data.get(key):
            continue

        value = (
            default()
            if callable(default)
            else default
        )

        if value is None:
            raise RuntimeError(
                f"Unable to determine value for "
                f"atlas/{path}:{key}"
            )

        data[key] = value
        changed = True

    if changed:
        try:
            client.kv_write(
                token,
                path,
                data,
            )
        except OpenBaoError as exc:
            raise RuntimeError(
                f"Unable to write OpenBao secret "
                f"atlas/{path}: {exc}"
            ) from exc

        if created:
            ok(
                f"Created OpenBao secret: "
                f"atlas/{path}"
            )
        else:
            ok(
                f"Completed OpenBao secret: "
                f"atlas/{path}"
            )

    else:
        ok(
            f"OpenBao secret exists: "
            f"atlas/{path}"
        )

    return data


def load_or_create_netbox_secrets(
    config,
    client,
    token,
):
    header(
        "NETBOX SECRETS"
    )

    postgres = ensure_secret_path(
        client,
        token,
        "netbox/postgres",
        {
            "password":
                (lambda: generate_secret(32)),
        },
    )

    redis = ensure_secret_path(
        client,
        token,
        "netbox/redis",
        {
            "password":
                (lambda: generate_secret(32)),
        },
    )

    redis_cache = ensure_secret_path(
        client,
        token,
        "netbox/redis-cache",
        {
            "password":
                (lambda: generate_secret(32)),
        },
    )

    application = ensure_secret_path(
        client,
        token,
        "netbox/application",
        {
            "secret_key":
                (lambda: generate_secret(64)),
            "api_token_pepper":
                (lambda: generate_secret(48)),
        },
    )

    configured_admin_name = config[
        "bootstrap"
    ][
        "superuser_name"
    ]

    admin = ensure_secret_path(
        client,
        token,
        "netbox/admin",
        {
            "username": configured_admin_name,
            "password":
                (lambda: generate_secret(24)),
        },
    )

    if (
        admin["username"]
        != configured_admin_name
    ):
        info(
            "OpenBao already contains NetBox "
            "admin username "
            f"'{admin['username']}'. "
            "Keeping the stored value instead "
            "of changing an existing deployment."
        )

    state = {
        "postgres_password":
            postgres["password"],
        "redis_password":
            redis["password"],
        "redis_cache_password":
            redis_cache["password"],
        "netbox_secret_key":
            application["secret_key"],
        "api_token_pepper":
            application["api_token_pepper"],
        "superuser_name":
            admin["username"],
        "superuser_password":
            admin["password"],
    }

    api = openbao_read_optional(
        client,
        token,
        "netbox/api",
    )

    if (
        api
        and
        api.get("token")
    ):
        state["netbox_api_token"] = api["token"]

    ok(
        "NetBox credentials loaded "
        "from OpenBao"
    )

    return state


def configure_compose_secret_env(
    state,
):
    COMPOSE_SECRET_ENV.clear()

    COMPOSE_SECRET_ENV.update(
        {
            "ATLAS_NETBOX_POSTGRES_PASSWORD":
                state[
                    "postgres_password"
                ],
            "ATLAS_NETBOX_REDIS_PASSWORD":
                state[
                    "redis_password"
                ],
            "ATLAS_NETBOX_REDIS_CACHE_PASSWORD":
                state[
                    "redis_cache_password"
                ],
            "ATLAS_NETBOX_SECRET_KEY":
                state[
                    "netbox_secret_key"
                ],
            "ATLAS_NETBOX_API_TOKEN_PEPPER":
                state[
                    "api_token_pepper"
                ],
            "ATLAS_NETBOX_SUPERUSER_NAME":
                state[
                    "superuser_name"
                ],
            "ATLAS_NETBOX_SUPERUSER_PASSWORD":
                state[
                    "superuser_password"
                ],
        }
    )

# ===========================================================================
# Docker Compose override
# ===========================================================================

def write_compose_override(
    config,
    runtime_dir,
    state,
):
    image = config[
        "netbox"
    ][
        "docker_image"
    ]

    listen_address = str(
        config[
            "network"
        ][
            "listen_address"
        ]
    )

    port = int(
        config[
            "network"
        ][
            "port"
        ]
    )

    if listen_address != "127.0.0.1":
        raise RuntimeError("NetBox bootstrap port must bind to 127.0.0.1; use Nginx for external access")

    # Secrets are deliberately NOT written into this
    # generated Compose file. Docker Compose resolves
    # these placeholders from the deploy process
    # environment prepared by configure_compose_secret_env().
    netbox_environment = {
        "DB_PASSWORD":
            "${ATLAS_NETBOX_POSTGRES_PASSWORD}",
        "REDIS_PASSWORD":
            "${ATLAS_NETBOX_REDIS_PASSWORD}",
        "REDIS_CACHE_PASSWORD":
            "${ATLAS_NETBOX_REDIS_CACHE_PASSWORD}",
        "SECRET_KEY":
            "${ATLAS_NETBOX_SECRET_KEY}",
        "API_TOKEN_PEPPER_1":
            "${ATLAS_NETBOX_API_TOKEN_PEPPER}",
        "SKIP_SUPERUSER":
            "false",
        "SUPERUSER_NAME":
            "${ATLAS_NETBOX_SUPERUSER_NAME}",
        "SUPERUSER_PASSWORD":
            "${ATLAS_NETBOX_SUPERUSER_PASSWORD}",
    }

    worker_environment = {
        "DB_PASSWORD":
            "${ATLAS_NETBOX_POSTGRES_PASSWORD}",
        "REDIS_PASSWORD":
            "${ATLAS_NETBOX_REDIS_PASSWORD}",
        "REDIS_CACHE_PASSWORD":
            "${ATLAS_NETBOX_REDIS_CACHE_PASSWORD}",
        "SECRET_KEY":
            "${ATLAS_NETBOX_SECRET_KEY}",
        "API_TOKEN_PEPPER_1":
            "${ATLAS_NETBOX_API_TOKEN_PEPPER}",
        "SKIP_SUPERUSER":
            "true",
    }

    proxy_file = Path(os.environ.get("ATLAS_PROXY_CONFIG", ROOT / ".runtime/proxy.yaml"))
    if proxy_file.exists():
        proxy = load_proxy_config(proxy_file, validate_tls=False)
        hostname = proxy["services"]["netbox"]["hostname"]
        https_port = proxy["proxy"]["https_port"]
        origin = f"https://{hostname}" + (f":{https_port}" if https_port != 443 else "")
        for environment in (netbox_environment, worker_environment):
            environment["ALLOWED_HOSTS"] = f"{hostname} atlas-netbox localhost 127.0.0.1"
            environment["CSRF_TRUSTED_ORIGINS"] = origin
        write_private(
            runtime_dir / "configuration/atlas_proxy.py",
            'SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")\n'
            'SESSION_COOKIE_SECURE = True\nCSRF_COOKIE_SECURE = True\n',
            0o644,
        )

    override = {
        "services": {
            "netbox": {
                "image": image,
                "networks": {
                    "default": {},
                    "atlas_web": {"aliases": ["atlas-netbox"]},
                },
                "restart":
                    "unless-stopped",
                "ports": [
                    (
                        f"{listen_address}:"
                        f"{port}:8080"
                    )
                ],
                "environment":
                    netbox_environment,
                "healthcheck": {
                    "start_period":
                        "180s",
                },
            },
            "netbox-worker": {
                "image": image,
                "restart":
                    "unless-stopped",
                "environment":
                    worker_environment,
            },
            "postgres": {
                "restart":
                    "unless-stopped",
                "environment": {
                    "POSTGRES_PASSWORD":
                        "${ATLAS_NETBOX_POSTGRES_PASSWORD}",
                },
            },
            "redis": {
                "restart":
                    "unless-stopped",
                "environment": {
                    "REDIS_PASSWORD":
                        "${ATLAS_NETBOX_REDIS_PASSWORD}",
                },
            },
            "redis-cache": {
                "restart":
                    "unless-stopped",
                "environment": {
                    "REDIS_PASSWORD":
                        "${ATLAS_NETBOX_REDIS_CACHE_PASSWORD}",
                },
            },
        },
        "networks": {
            "atlas_web": {"external": True, "name": NETBOX_NETWORK},
        },
    }

    override_file = (
        runtime_dir
        / "docker-compose.override.yml"
    )

    with override_file.open(
        "w",
        encoding="utf-8",
    ) as handle:
        yaml.safe_dump(
            override,
            handle,
            sort_keys=False,
            default_flow_style=False,
        )

    os.chmod(
        override_file,
        0o600,
    )

    prepare_configuration_permissions(runtime_dir)

    ok(
        f"Generated {override_file} "
        "without plaintext secrets"
    )

    return override_file


# ===========================================================================
# Docker Compose
# ===========================================================================

def compose(
    runtime_dir,
    args,
    capture=False,
    check=True,
):
    env = os.environ.copy()
    env.update(
        COMPOSE_SECRET_ENV
    )

    return run(
        [
            "docker",
            "compose",
            *args,
        ],
        cwd=runtime_dir,
        capture=capture,
        check=check,
        env=env,
    )


def pull_images(
    runtime_dir,
):
    header(
        "DOCKER IMAGE PULL"
    )

    compose(
        runtime_dir,
        [
            "pull",
        ],
    )

    ok(
        "Docker images pulled"
    )


def verify_netbox_configuration_mount(
    runtime_dir,
):
    header(
        "NETBOX CONFIG PREFLIGHT"
    )

    # Run the check through Compose so it uses the exact NetBox image, user,
    # bind mount and SELinux mount options that the real service will use.
    result = compose(
        runtime_dir,
        [
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/bin/sh",
            "netbox",
            "-ec",
            (
                "test -x /etc/netbox/config; "
                "test -r /etc/netbox/config/configuration.py; "
                "for file in /etc/netbox/config/*.py; do "
                "test -r \"$file\"; "
                "done"
            ),
        ],
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        if result.stdout:
            print(
                result.stdout.rstrip()
            )

        if result.stderr:
            print(
                result.stderr.rstrip(),
                file=sys.stderr,
            )

        configuration_dir = (
            runtime_dir
            / "configuration"
        )

        try:
            directory_mode = (
                configuration_dir.stat().st_mode
                & 0o777
            )
            mode_text = f"{directory_mode:04o}"
        except OSError:
            mode_text = "unknown"

        raise RuntimeError(
            "NetBox container cannot read its configuration bind mount "
            f"(host directory mode {mode_text}: {configuration_dir})"
        )

    ok(
        "NetBox container can read /etc/netbox/config"
    )


def start_netbox_core(
    runtime_dir,
):
    header(
        "START NETBOX CORE"
    )

    compose(
        runtime_dir,
        [
            "up",
            "-d",
            "postgres",
            "redis",
            "redis-cache",
            "netbox",
        ],
    )

    ok(
        "NetBox core services started"
    )


def start_netbox_worker(
    runtime_dir,
):
    header(
        "START NETBOX WORKER"
    )

    compose(
        runtime_dir,
        [
            "up",
            "-d",
            "netbox-worker",
        ],
    )

    ok(
        "NetBox worker started"
    )


# ===========================================================================
# Health
# ===========================================================================

def get_netbox_container_id(
    runtime_dir,
):
    result = compose(
        runtime_dir,
        [
            "ps",
            "-q",
            "netbox",
        ],
        capture=True,
    )

    return result.stdout.strip()


def container_state(
    container_id,
):
    result = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}",
            container_id,
        ],
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        return "unknown"

    return result.stdout.strip()


def container_health(
    container_id,
):
    result = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            container_id,
        ],
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        return "unknown"

    return result.stdout.strip()


def wait_for_netbox(
    runtime_dir,
    timeout,
    poll_interval,
):
    header(
        "WAIT FOR NETBOX"
    )

    start = time.time()

    last_health = None
    last_state = None

    while True:
        elapsed = int(
            time.time() - start
        )

        container_id = (
            get_netbox_container_id(
                runtime_dir
            )
        )

        if not container_id:
            state = "container-not-found"
            health = "unknown"

        else:
            state = (
                container_state(
                    container_id
                )
            )

            health = (
                container_health(
                    container_id
                )
            )

        if state != last_state:
            info(
                f"NetBox state: "
                f"{state}"
            )

            last_state = state

        if health != last_health:
            info(
                f"NetBox health: "
                f"{health}"
            )

            last_health = health

        if (
            state == "running"
            and
            health == "healthy"
        ):
            ok(
                f"NetBox is healthy "
                f"after {elapsed}s"
            )

            return

        if state in {
            "exited",
            "dead",
        }:
            print()
            print(
                "[ERROR] NetBox container "
                f"entered state: {state}"
            )

            print()
            print(
                "Recent NetBox logs:"
            )

            compose(
                runtime_dir,
                [
                    "logs",
                    "--tail",
                    "100",
                    "netbox",
                ],
                check=False,
            )

            raise RuntimeError(
                "NetBox container stopped "
                "during startup"
            )

        if elapsed > timeout:
            print()
            print(
                "[ERROR] NetBox did not "
                f"become healthy within "
                f"{timeout} seconds"
            )

            print()
            print(
                "Docker Compose status:"
            )

            compose(
                runtime_dir,
                [
                    "ps",
                ],
                check=False,
            )

            print()
            print(
                "Recent NetBox logs:"
            )

            compose(
                runtime_dir,
                [
                    "logs",
                    "--tail",
                    "100",
                    "netbox",
                ],
                check=False,
            )

            raise RuntimeError(
                "Timed out waiting "
                "for NetBox"
            )

        time.sleep(
            poll_interval
        )


# ===========================================================================
# NetBox API bootstrap
# ===========================================================================

def get_netbox_url(config):
    address = str(
        config[
            "network"
        ][
            "listen_address"
        ]
    )

    port = int(
        config[
            "network"
        ][
            "port"
        ]
    )

    if address in {
        "0.0.0.0",
        "::",
    }:
        address = "127.0.0.1"

    return (
        f"http://{address}:{port}"
    )


def token_headers(token):
    if token.startswith("nbt_"):
        authorization = (
            f"Bearer {token}"
        )
    else:
        authorization = (
            f"Token {token}"
        )

    return {
        "Authorization":
            authorization,
        "Accept":
            "application/json",
    }


def validate_api_token(
    base_url,
    token,
):
    try:
        response = requests.get(
            (
                f"{base_url}"
                "/api/users/users/"
                "?limit=1"
            ),
            headers=token_headers(
                token
            ),
            timeout=15,
        )

    except requests.RequestException:
        return False

    return (
        response.status_code
        == 200
    )


def provision_api_token(
    config,
    state,
    openbao_client,
    openbao_token,
):
    header(
        "NETBOX API TOKEN"
    )

    base_url = (
        get_netbox_url(
            config
        )
    )

    api_secret = openbao_read_optional(
        openbao_client,
        openbao_token,
        "netbox/api",
    )

    existing_token = None

    if api_secret:
        existing_token = api_secret.get(
            "token"
        )

    if existing_token:
        info(
            "Validating NetBox API token "
            "stored in OpenBao"
        )

        if validate_api_token(
            base_url,
            existing_token,
        ):
            ok(
                "Stored NetBox API token "
                "is valid"
            )

            return existing_token

        info(
            "Stored API token is no longer "
            "valid; provisioning a new one"
        )

    endpoint = (
        f"{base_url}"
        "/api/users/tokens/provision/"
    )

    payload = {
        "username":
            state[
                "superuser_name"
            ],
        "password":
            state[
                "superuser_password"
            ],
    }

    info(
        "Provisioning NetBox API token"
    )

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "Accept":
                    "application/json",
            },
            timeout=30,
        )

    except requests.RequestException as exc:
        raise RuntimeError(
            "Unable to contact NetBox token "
            "provisioning endpoint: "
            f"{exc}"
        ) from exc

    if response.status_code not in {
        200,
        201,
    }:
        raise RuntimeError(
            "NetBox API token provisioning "
            f"failed with HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    try:
        token_data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "NetBox returned an invalid "
            "token response"
        ) from exc

    version = token_data.get(
        "version"
    )

    key = token_data.get(
        "key"
    )

    plaintext = token_data.get(
        "token"
    )

    if (
        version == 2
        or
        (
            key
            and
            plaintext
        )
    ):
        if not key:
            raise RuntimeError(
                "NetBox v2 token response "
                "does not contain a key"
            )

        if not plaintext:
            raise RuntimeError(
                "NetBox v2 token response "
                "does not contain plaintext"
            )

        api_token = (
            f"nbt_{key}."
            f"{plaintext}"
        )

    else:
        api_token = (
            plaintext
            or
            key
        )

    if not api_token:
        raise RuntimeError(
            "NetBox API token response did "
            "not contain usable credentials"
        )

    if not validate_api_token(
        base_url,
        api_token,
    ):
        raise RuntimeError(
            "New NetBox API token failed "
            "validation"
        )

    try:
        openbao_client.kv_write(
            openbao_token,
            "netbox/api",
            {
                "token": api_token,
            },
        )
    except OpenBaoError as exc:
        raise RuntimeError(
            "NetBox API token was created "
            "but could not be stored in "
            f"OpenBao: {exc}"
        ) from exc

    state[
        "netbox_api_token"
    ] = api_token

    ok(
        "NetBox API token provisioned "
        "and validated"
    )

    ok(
        "API token stored in OpenBao at "
        "atlas/netbox/api"
    )

    return api_token


def run_atlas_bootstrap(
    config,
    api_token,
    verbose=False,
):
    header(
        "ATLAS NETBOX BOOTSTRAP"
    )

    bootstrap_script = (
        ROOT
        / "scripts"
        / "bootstrap_netbox.py"
    )

    if not bootstrap_script.exists():
        raise RuntimeError(
            "Atlas bootstrap script "
            "not found"
        )

    base_url = (
        get_netbox_url(
            config
        )
    )

    env = os.environ.copy()

    env[
        "NETBOX_URL"
    ] = base_url

    env[
        "NETBOX_TOKEN"
    ] = api_token

    info(
        f"Target NetBox: "
        f"{base_url}"
    )

    if verbose:
        info(
            "Running Atlas bootstrap "
            "in verbose mode"
        )

        run(
            [
                sys.executable,
                str(
                    bootstrap_script
                ),
                "--apply",
            ],
            cwd=ROOT,
            env=env,
        )

    else:
        info(
            "Running Atlas bootstrap"
        )

        result = subprocess.run(
            [
                sys.executable,
                str(
                    bootstrap_script
                ),
                "--apply",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

        if result.returncode != 0:
            print()
            print(
                "[ERROR] Atlas bootstrap failed"
            )

            if result.stdout:
                print()
                print(result.stdout)

            if result.stderr:
                print()
                print(
                    result.stderr,
                    file=sys.stderr,
                )

            raise RuntimeError(
                "Atlas bootstrap failed"
            )

    ok(
        "Atlas baseline and "
        "Device Catalog are current"
    )

    ok(
        "Atlas bootstrap completed"
    )


# ===========================================================================
# Final output
# ===========================================================================

def print_result(
    config,
    state,
):
    address = str(
        config[
            "network"
        ][
            "listen_address"
        ]
    )

    port = int(
        config[
            "network"
        ][
            "port"
        ]
    )

    if address in {
        "0.0.0.0",
        "::",
    }:
        browser_address = (
            "127.0.0.1"
        )
    else:
        browser_address = (
            address
        )

    header(
        "NETBOX DOCKER READY"
    )

    print(
        f"URL:      "
        f"http://{browser_address}:"
        f"{port}"
    )

    print(
        f"Username: "
        f"{state['superuser_name']}"
    )

    print()
    print(
        "Credentials source of truth:"
    )
    print(
        "  OpenBao KV v2: atlas/netbox/"
    )

# ===========================================================================
# Main
# ===========================================================================

def main():
    args = parse_args()

    try:
        config = load_config()

        compose_project = config[
            "runtime"
        ][
            "compose_project"
        ]

        os.environ[
            "COMPOSE_PROJECT_NAME"
        ] = compose_project

        header(
            "ATLAS NETBOX DOCKER DEPLOY"
        )

        print(
            f"NetBox:        "
            f"{config['netbox']['version']}"
        )

        print(
            f"Docker image:  "
            f"{config['netbox']['docker_image']}"
        )

        print(
            f"NetBox Docker: "
            f"{config['netbox_docker']['ref']}"
        )

        print(
            f"Compose project: "
            f"{compose_project}"
        )

        check_docker()

        runtime_dir = (
            prepare_netbox_docker(
                config
            )
        )

        (
            openbao_client,
            openbao_token,
        ) = connect_openbao()

        state = load_or_create_netbox_secrets(
            config,
            openbao_client,
            openbao_token,
        )

        configure_compose_secret_env(
            state
        )

        write_compose_override(
            config,
            runtime_dir,
            state,
        )

        ensure_netbox_configuration_permissions(
            runtime_dir
        )

        if args.prepare_only:
            header(
                "PREPARE COMPLETE"
            )

            print(
                "[OK] Runtime prepared."
            )

            print()
            print(
                "No containers were "
                "started."
            )

            return

        pull_images(
            runtime_dir
        )

        ensure_network(NETBOX_NETWORK)

        verify_netbox_configuration_mount(
            runtime_dir
        )

        start_netbox_core(
            runtime_dir
        )

        startup_timeout = int(
            config[
                "runtime"
            ][
                "startup_timeout"
            ]
        )

        poll_interval = int(
            config[
                "runtime"
            ][
                "poll_interval"
            ]
        )

        wait_for_netbox(
            runtime_dir,
            timeout=startup_timeout,
            poll_interval=poll_interval,
        )

        start_netbox_worker(
            runtime_dir
        )

        api_token = (
            provision_api_token(
                config,
                state,
                openbao_client,
                openbao_token,
            )
        )

        run_atlas_bootstrap(
            config,
            api_token,
            verbose=args.verbose,
        )

        print_result(
            config,
            state,
        )

    except KeyboardInterrupt:
        print()
        raise SystemExit(
            "[ERROR] Interrupted"
        )

    except Exception as exc:
        print()
        raise SystemExit(
            f"[ERROR] {exc}"
        )


if __name__ == "__main__":
    main()
