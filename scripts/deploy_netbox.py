#!/usr/bin/env python3

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import yaml
import requests


ROOT = Path(__file__).resolve().parent.parent

CONFIG_FILE = (
    ROOT
    / "deployment"
    / "netbox.yaml"
)

STATE_FILE = (
    ROOT
    / ".runtime"
    / "atlas-netbox-state.json"
)


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


# ===========================================================================
# Runtime secrets
# ===========================================================================

def generate_secret(
    length=32,
):
    return secrets.token_urlsafe(
        length
    )


def load_or_create_state(
    config,
):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    created = False

    if STATE_FILE.exists():
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as handle:
            state = json.load(
                handle
            )

        if not isinstance(
            state,
            dict,
        ):
            raise RuntimeError(
                "Runtime state file "
                "is invalid"
            )

    else:
        state = {}
        created = True

    defaults = {
        "postgres_password":
            generate_secret(32),

        "redis_password":
            generate_secret(32),

        "redis_cache_password":
            generate_secret(32),

        "netbox_secret_key":
            generate_secret(64),

        "api_token_pepper":
            generate_secret(48),

        "superuser_password":
            generate_secret(24),
    }

    changed = False

    for key, value in (
        defaults.items()
    ):
        if key not in state:
            state[key] = value
            changed = True

    state[
        "superuser_name"
    ] = config[
        "bootstrap"
    ][
        "superuser_name"
    ]

    if (
        created
        or
        changed
    ):
        with STATE_FILE.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                state,
                handle,
                indent=2,
            )

            handle.write("\n")

        os.chmod(
            STATE_FILE,
            0o600,
        )

    ok(
        f"Runtime secrets: "
        f"{STATE_FILE.relative_to(ROOT)}"
    )

    return (
        state,
        created,
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

    netbox_environment = {
        "DB_PASSWORD":
            state[
                "postgres_password"
            ],

        "REDIS_PASSWORD":
            state[
                "redis_password"
            ],

        "REDIS_CACHE_PASSWORD":
            state[
                "redis_cache_password"
            ],

        "SECRET_KEY":
            state[
                "netbox_secret_key"
            ],

        "API_TOKEN_PEPPER_1":
            state[
                "api_token_pepper"
            ],

        "SKIP_SUPERUSER":
            "false",

        "SUPERUSER_NAME":
            state[
                "superuser_name"
            ],

        "SUPERUSER_PASSWORD":
            state[
                "superuser_password"
            ],
    }

    worker_environment = {
        "DB_PASSWORD":
            state[
                "postgres_password"
            ],

        "REDIS_PASSWORD":
            state[
                "redis_password"
            ],

        "REDIS_CACHE_PASSWORD":
            state[
                "redis_cache_password"
            ],

        "SECRET_KEY":
            state[
                "netbox_secret_key"
            ],

        "API_TOKEN_PEPPER_1":
            state[
                "api_token_pepper"
            ],

        # Worker must not try to create
        # the same superuser.
        "SKIP_SUPERUSER":
            "true",
    }

    override = {
        "services": {
            "netbox": {
                "image": image,

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
                        state[
                            "postgres_password"
                        ],
                },
            },

            "redis": {
                "restart":
                    "unless-stopped",

                "environment": {
                    "REDIS_PASSWORD":
                        state[
                            "redis_password"
                        ],
                },
            },

            "redis-cache": {
                "restart":
                    "unless-stopped",

                "environment": {
                    "REDIS_PASSWORD":
                        state[
                            "redis_cache_password"
                        ],
                },
            },
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

    ok(
        f"Generated "
        f"{override_file}"
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
    return run(
        [
            "docker",
            "compose",
            *args,
        ],
        cwd=runtime_dir,
        capture=capture,
        check=check,
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


def start_stack(
    runtime_dir,
):
    header(
        "START NETBOX"
    )

    compose(
        runtime_dir,
        [
            "up",
            "-d",
        ],
    )

    ok(
        "Docker Compose stack started"
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
    timeout=360,
):
    header(
        "WAIT FOR NETBOX"
    )

    start = time.time()

    last_status = None

    while True:
        container_id = (
            get_netbox_container_id(
                runtime_dir
            )
        )

        if container_id:
            status = (
                container_health(
                    container_id
                )
            )

        else:
            status = (
                "container-not-found"
            )

        if status != last_status:
            info(
                f"NetBox health: "
                f"{status}"
            )

            last_status = status

        if status == "healthy":
            ok(
                "NetBox container is healthy"
            )

            return

        if (
            time.time()
            - start
            > timeout
        ):
            print()
            print(
                "[ERROR] NetBox did not "
                "become healthy"
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

        time.sleep(5)


# ===========================================================================
# NetBox API bootstrap
# ===========================================================================

def save_state(state):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            indent=2,
        )

        handle.write("\n")

    os.chmod(
        STATE_FILE,
        0o600,
    )


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
):
    header(
        "NETBOX API TOKEN"
    )

    base_url = (
        get_netbox_url(
            config
        )
    )

    existing_token = (
        state.get(
            "netbox_api_token"
        )
    )

    if existing_token:
        info(
            "Validating stored "
            "NetBox API token"
        )

        if validate_api_token(
            base_url,
            existing_token,
        ):
            ok(
                "Stored NetBox API "
                "token is valid"
            )

            return existing_token

        info(
            "Stored API token is "
            "no longer valid; "
            "provisioning a new one"
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
        "Provisioning NetBox "
        "API token"
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
            "Unable to contact "
            "NetBox token "
            "provisioning endpoint: "
            f"{exc}"
        )

    if response.status_code not in {
        200,
        201,
    }:
        raise RuntimeError(
            "NetBox API token "
            "provisioning failed "
            f"with HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    try:
        token_data = (
            response.json()
        )

    except ValueError as exc:
        raise RuntimeError(
            "NetBox returned an "
            "invalid token response"
        ) from exc

    version = (
        token_data.get(
            "version"
        )
    )

    key = (
        token_data.get(
            "key"
        )
    )

    plaintext = (
        token_data.get(
            "token"
        )
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
                "NetBox v2 token "
                "response does not "
                "contain a key"
            )

        if not plaintext:
            raise RuntimeError(
                "NetBox v2 token "
                "response does not "
                "contain plaintext"
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
            "NetBox API token "
            "response did not "
            "contain usable "
            "credentials"
        )

    if not validate_api_token(
        base_url,
        api_token,
    ):
        raise RuntimeError(
            "New NetBox API token "
            "failed validation"
        )

    state[
        "netbox_api_token"
    ] = api_token

    save_state(
        state
    )

    ok(
        "NetBox API token "
        "provisioned and validated"
    )

    ok(
        "API token stored in "
        "gitignored runtime state"
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
    state_created,
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

    if state_created:
        print()
        print(
            "Generated admin password:"
        )

        print(
            f"  "
            f"{state['superuser_password']}"
        )

    print()
    print(
        "Runtime credentials are stored in:"
    )

    print(
        f"  "
        f"{STATE_FILE}"
    )

    print()
    print(
        "The runtime state is gitignored "
        "and must not be committed."
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

        state, state_created = (
            load_or_create_state(
                config
            )
        )

        write_compose_override(
            config,
            runtime_dir,
            state,
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

        start_stack(
            runtime_dir
        )

        wait_for_netbox(
            runtime_dir
        )

        api_token = (
            provision_api_token(
                config,
                state,
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
            state_created,
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
