#!/usr/bin/env python3

import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests


# ===========================================================================
# Paths / configuration
# ===========================================================================

ROOT = Path(__file__).resolve().parent.parent

COMPOSE_FILE = (
    ROOT
    / "deployment"
    / "openbao"
    / "docker-compose.yml"
)

POLICY_FILE = (
    ROOT
    / "deployment"
    / "openbao"
    / "policies"
    / "atlas-deployer.hcl"
)

APPROLE_STATE_FILE = (
    ROOT
    / ".runtime"
    / "openbao-approle.json"
)

OPERATOR_POLICY_FILE = (
    ROOT
    / "deployment"
    / "openbao"
    / "policies"
    / "atlas-operator.hcl"
)

OPERATOR_STATE_FILE = (
    ROOT
    / ".runtime"
    / "openbao-operator.json"
)

DEVICE_CREDENTIALS_POLICY_FILE = (
    ROOT
    / "deployment"
    / "openbao"
    / "policies"
    / "device-credentials-read.hcl"
)

RUNTIME_ROLES = {
    "atlas-backend": {
        "policy_file": (
            ROOT
            / "deployment"
            / "openbao"
            / "policies"
            / "atlas-backend.hcl"
        ),
        "identity_file": (
            ROOT
            / ".runtime"
            / "openbao-backend.json"
        ),
        "additional_policies": [],
    },
    "netbox-runtime": {
        "policy_file": (
            ROOT
            / "deployment"
            / "openbao"
            / "policies"
            / "netbox-runtime.hcl"
        ),
        "identity_file": (
            ROOT
            / ".runtime"
            / "openbao-netbox.json"
        ),
        "additional_policies": [
            "device-credentials-read",
        ],
    },
    "checkmk-runtime": {
        "policy_file": (
            ROOT
            / "deployment"
            / "openbao"
            / "policies"
            / "checkmk-runtime.hcl"
        ),
        "identity_file": (
            ROOT
            / ".runtime"
            / "openbao-checkmk.json"
        ),
        "additional_policies": [
            "device-credentials-read",
        ],
    },
    "oxidized-runtime": {
        "policy_file": (
            ROOT
            / "deployment"
            / "openbao"
            / "policies"
            / "oxidized-runtime.hcl"
        ),
        "identity_file": (
            ROOT
            / ".runtime"
            / "openbao-oxidized.json"
        ),
        "additional_policies": [
            "device-credentials-read",
        ],
    },
}

OPENBAO_URL = "http://127.0.0.1:18200"

STARTUP_TIMEOUT = 60
POLL_INTERVAL = 2

UNSEAL_SHARES = 5
UNSEAL_THRESHOLD = 3


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
    capture=False,
    check=True,
):
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=capture,
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


def compose(
    args,
    capture=False,
    check=True,
):
    return run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            *args,
        ],
        capture=capture,
        check=check,
    )

def revoke_initial_root_token(root_token):
    header(
        "REVOKE INITIAL ROOT TOKEN"
    )

    api_request(
        "POST",
        "auth/token/revoke-self",
        token=root_token,
        expected=(200, 204),
    )

    ok(
        "Initial root token revoked"
    )

def ensure_operator_policy(root_token):
    if not OPERATOR_POLICY_FILE.exists():
        raise RuntimeError(
            f"Operator policy file not found: "
            f"{OPERATOR_POLICY_FILE}"
        )

    policy = OPERATOR_POLICY_FILE.read_text(
        encoding="utf-8"
    )

    api_request(
        "POST",
        "sys/policies/acl/atlas-operator",
        token=root_token,
        payload={
            "policy": policy,
        },
    )

    ok(
        "atlas-operator policy configured"
    )


def ensure_operator_role(root_token):
    api_request(
        "POST",
        "auth/approle/role/atlas-operator",
        token=root_token,
        payload={
            "bind_secret_id": True,
            "token_policies": [
                "atlas-operator",
            ],
            "secret_id_ttl": "0",
            "secret_id_num_uses": 0,
            "token_ttl": "15m",
            "token_max_ttl": "1h",
            "token_num_uses": 0,
        },
    )

    ok(
        "atlas-operator AppRole configured"
    )


def create_operator_identity(root_token):
    response = api_request(
        "GET",
        (
            "auth/approle/role/"
            "atlas-operator/role-id"
        ),
        token=root_token,
    )

    role_id = (
        response.json()
        ["data"]
        ["role_id"]
    )

    response = api_request(
        "POST",
        (
            "auth/approle/role/"
            "atlas-operator/secret-id"
        ),
        token=root_token,
        payload={},
    )

    secret_id = (
        response.json()
        ["data"]
        ["secret_id"]
    )

    OPERATOR_STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OPERATOR_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "role_id": role_id,
                "secret_id": secret_id,
            },
            handle,
            indent=2,
        )

        handle.write("\n")

    os.chmod(
        OPERATOR_STATE_FILE,
        0o600,
    )

    # Verify that authentication actually works.
    login_approle(
        role_id,
        secret_id,
    )

    ok(
        "atlas-operator machine identity "
        "created and validated"
    )

# ===========================================================================
# Docker
# ===========================================================================

def check_docker():
    header(
        "DOCKER PREFLIGHT"
    )

    if not COMPOSE_FILE.exists():
        raise RuntimeError(
            "OpenBao Compose file "
            f"not found: {COMPOSE_FILE}"
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
            "Docker daemon is not available"
        )

    ok(
        "Docker daemon reachable"
    )


def start_openbao():
    header(
        "START OPENBAO"
    )

    compose(
        [
            "up",
            "-d",
        ]
    )

    ok(
        "OpenBao container started"
    )


# ===========================================================================
# OpenBao API helpers
# ===========================================================================

def token_headers(token):
    return {
        "X-Vault-Token": token,
        "Accept": "application/json",
    }


def api_request(
    method,
    path,
    token=None,
    payload=None,
    expected=(200, 204),
):
    headers = {
        "Accept": "application/json",
    }

    if token:
        headers.update(
            token_headers(token)
        )

    response = requests.request(
        method,
        (
            f"{OPENBAO_URL}"
            f"/v1/{path.lstrip('/')}"
        ),
        headers=headers,
        json=payload,
        timeout=15,
    )

    if (
        response.status_code
        not in expected
    ):
        raise RuntimeError(
            f"OpenBao API {method} "
            f"{path} failed with HTTP "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response


# ===========================================================================
# OpenBao status
# ===========================================================================

def get_seal_status():
    response = requests.get(
        (
            f"{OPENBAO_URL}"
            "/v1/sys/seal-status"
        ),
        timeout=5,
    )

    response.raise_for_status()

    return response.json()


def wait_for_openbao():
    header(
        "WAIT FOR OPENBAO"
    )

    start = time.time()

    while True:
        try:
            status = get_seal_status()

            ok(
                "OpenBao API reachable"
            )

            return status

        except requests.RequestException:
            pass

        if (
            time.time() - start
            > STARTUP_TIMEOUT
        ):
            raise RuntimeError(
                "Timed out waiting for "
                "OpenBao API"
            )

        time.sleep(
            POLL_INTERVAL
        )


def get_leader_status():
    response = requests.get(
        (
            f"{OPENBAO_URL}"
            "/v1/sys/leader"
        ),
        timeout=5,
    )

    response.raise_for_status()

    return response.json()


def wait_for_raft_leader():
    header(
        "WAIT FOR OPENBAO RAFT"
    )

    start = time.time()

    while True:
        try:
            status = get_leader_status()

            leader_address = status.get(
                "leader_address"
            )

            if leader_address:
                ok(
                    "OpenBao Raft leader elected"
                )

                return status

        except requests.RequestException:
            pass

        if (
            time.time() - start
            > STARTUP_TIMEOUT
        ):
            raise RuntimeError(
                "Timed out waiting for "
                "OpenBao Raft leader election"
            )

        time.sleep(
            POLL_INTERVAL
        )


# ===========================================================================
# Initialization
# ===========================================================================

def send_initial_keys_to_parent(keys):
    """Send fresh-install unseal keys to deploy_atlas.py via an inherited pipe.

    The keys are never written to disk, argv, or environment variables.
    If deploy_openbao.py is run standalone, no pipe is configured and this
    function is a no-op.
    """
    fd_value = os.environ.get(
        "ATLAS_UNSEAL_KEYS_FD"
    )

    if not fd_value:
        return

    try:
        fd = int(fd_value)
    except ValueError as exc:
        raise RuntimeError(
            "ATLAS_UNSEAL_KEYS_FD is invalid"
        ) from exc

    payload = json.dumps(
        {
            "keys": keys,
            "shares": UNSEAL_SHARES,
            "threshold": UNSEAL_THRESHOLD,
        }
    ).encode("utf-8")

    try:
        os.write(
            fd,
            payload,
        )
    except OSError as exc:
        raise RuntimeError(
            "Unable to pass OpenBao unseal keys "
            "to the Atlas orchestrator"
        ) from exc


def initialize_openbao():
    header(
        "INITIALIZE OPENBAO"
    )

    response = requests.post(
        (
            f"{OPENBAO_URL}"
            "/v1/sys/init"
        ),
        json={
            "secret_shares":
                UNSEAL_SHARES,

            "secret_threshold":
                UNSEAL_THRESHOLD,
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    keys = data.get(
        "keys"
    )

    root_token = data.get(
        "root_token"
    )

    if (
        not keys
        or
        len(keys) != UNSEAL_SHARES
    ):
        raise RuntimeError(
            "OpenBao did not return "
            "the expected unseal keys"
        )

    if not root_token:
        raise RuntimeError(
            "OpenBao did not return "
            "an initial root token"
        )

    print()
    print(
        "IMPORTANT: OPENBAO UNSEAL KEYS"
    )
    print()
    print(
        "Store these keys outside "
        "this Atlas server."
    )
    print(
        "They will NOT be stored "
        "by Atlas."
    )
    print()

    for index, key in enumerate(
        keys,
        start=1,
    ):
        print(
            f"Unseal Key {index}: "
            f"{key}"
        )

    print()
    print(
        f"Threshold: "
        f"{UNSEAL_THRESHOLD} "
        f"of {UNSEAL_SHARES}"
    )
    print()

    return (
        root_token,
        keys,
    )


# ===========================================================================
# Unseal
# ===========================================================================

def unseal_with_keys(keys):
    header(
        "OPENBAO INITIAL UNSEAL"
    )

    status = get_seal_status()

    for key in keys:
        response = requests.post(
            (
                f"{OPENBAO_URL}"
                "/v1/sys/unseal"
            ),
            json={
                "key": key,
            },
            timeout=10,
        )

        response.raise_for_status()

        status = response.json()

        if not status["sealed"]:
            break

    if status["sealed"]:
        raise RuntimeError(
            "OpenBao initial unseal failed"
        )

    ok(
        "OpenBao is unsealed"
    )

    return status


def unseal_openbao(status):
    if not status["sealed"]:
        ok(
            "OpenBao is already unsealed"
        )

        return status

    threshold = int(
        status["t"]
    )

    total = int(
        status["n"]
    )

    header(
        "OPENBAO UNSEAL"
    )

    print(
        f"OpenBao requires "
        f"{threshold} of {total} "
        f"unseal keys."
    )

    while status["sealed"]:
        progress = int(
            status.get(
                "progress",
                0,
            )
        )

        key = getpass.getpass(
            f"Unseal key "
            f"{progress + 1}/"
            f"{threshold}: "
        )

        if not key:
            raise RuntimeError(
                "Empty unseal key"
            )

        response = requests.post(
            (
                f"{OPENBAO_URL}"
                "/v1/sys/unseal"
            ),
            json={
                "key": key,
            },
            timeout=10,
        )

        response.raise_for_status()

        status = response.json()

        if status["sealed"]:
            info(
                "Unseal progress: "
                f"{status['progress']}/"
                f"{status['t']}"
            )

    ok(
        "OpenBao is unsealed"
    )

    return status


# ===========================================================================
# Atlas KV
# ===========================================================================

def ensure_atlas_kv(root_token):
    info(
        "Checking Atlas KV v2 "
        "secrets engine"
    )

    response = api_request(
        "GET",
        "sys/mounts",
        token=root_token,
    )

    body = response.json()

    mounts = body.get(
        "data",
        body,
    )

    existing = mounts.get(
        "atlas/"
    )

    if existing:
        if (
            existing.get("type")
            != "kv"
        ):
            raise RuntimeError(
                "Existing atlas/ mount "
                "is not KV"
            )

        version = str(
            existing
            .get(
                "options",
                {},
            )
            .get(
                "version",
                "",
            )
        )

        if version != "2":
            raise RuntimeError(
                "Existing atlas/ mount "
                "is not KV v2"
            )

        ok(
            "Atlas KV v2 already enabled"
        )

        return

    api_request(
        "POST",
        "sys/mounts/atlas",
        token=root_token,
        payload={
            "type": "kv",
            "options": {
                "version": "2",
            },
        },
    )

    ok(
        "Atlas KV v2 enabled"
    )


# ===========================================================================
# Policy
# ===========================================================================

def ensure_atlas_policy(root_token):
    if not POLICY_FILE.exists():
        raise RuntimeError(
            "Policy file not found: "
            f"{POLICY_FILE}"
        )

    policy = POLICY_FILE.read_text(
        encoding="utf-8"
    )

    if not policy.strip():
        raise RuntimeError(
            "atlas-deployer policy "
            "is empty"
        )

    api_request(
        "POST",
        (
            "sys/policies/acl/"
            "atlas-deployer"
        ),
        token=root_token,
        payload={
            "policy": policy,
        },
    )

    ok(
        "atlas-deployer policy configured"
    )


def ensure_runtime_policy(
    root_token,
    role_name,
    policy_file,
):
    if not policy_file.exists():
        raise RuntimeError(
            f"Runtime policy file not found: {policy_file}"
        )

    policy = policy_file.read_text(
        encoding="utf-8"
    )

    if not policy.strip():
        raise RuntimeError(
            f"Runtime policy {role_name} is empty"
        )

    api_request(
        "POST",
        f"sys/policies/acl/{role_name}",
        token=root_token,
        payload={
            "policy": policy,
        },
    )

    ok(
        f"{role_name} policy configured"
    )


# ===========================================================================
# AppRole
# ===========================================================================

def ensure_approle_auth(root_token):
    info(
        "Checking AppRole auth method"
    )

    response = api_request(
        "GET",
        "sys/auth",
        token=root_token,
    )

    body = response.json()

    auth_methods = body.get(
        "data",
        body,
    )

    existing = auth_methods.get(
        "approle/"
    )

    if existing:
        if (
            existing.get("type")
            != "approle"
        ):
            raise RuntimeError(
                "auth/approle already "
                "exists with a different "
                "auth type"
            )

        ok(
            "AppRole auth method "
            "already enabled"
        )

        return

    api_request(
        "POST",
        "sys/auth/approle",
        token=root_token,
        payload={
            "type": "approle",
        },
    )

    ok(
        "AppRole auth method enabled"
    )


def ensure_atlas_deployer_role(
    root_token,
):
    api_request(
        "POST",
        (
            "auth/approle/role/"
            "atlas-deployer"
        ),
        token=root_token,
        payload={
            "bind_secret_id":
                True,

            "token_policies": [
                "atlas-deployer",
            ],

            "secret_id_ttl":
                "0",

            "secret_id_num_uses":
                0,

            "token_ttl":
                "15m",

            "token_max_ttl":
                "1h",

            "token_num_uses":
                0,
        },
    )

    ok(
        "atlas-deployer AppRole configured"
    )


def get_role_id(root_token):
    response = api_request(
        "GET",
        (
            "auth/approle/role/"
            "atlas-deployer/role-id"
        ),
        token=root_token,
    )

    role_id = (
        response
        .json()
        .get(
            "data",
            {},
        )
        .get(
            "role_id"
        )
    )

    if not role_id:
        raise RuntimeError(
            "OpenBao did not return "
            "AppRole RoleID"
        )

    return role_id


def generate_secret_id(root_token):
    response = api_request(
        "POST",
        (
            "auth/approle/role/"
            "atlas-deployer/secret-id"
        ),
        token=root_token,
        payload={},
    )

    secret_id = (
        response
        .json()
        .get(
            "data",
            {},
        )
        .get(
            "secret_id"
        )
    )

    if not secret_id:
        raise RuntimeError(
            "OpenBao did not return "
            "AppRole SecretID"
        )

    return secret_id


def login_approle(
    role_id,
    secret_id,
):
    response = api_request(
        "POST",
        "auth/approle/login",
        payload={
            "role_id": role_id,
            "secret_id": secret_id,
        },
    )

    token = (
        response
        .json()
        .get(
            "auth",
            {},
        )
        .get(
            "client_token"
        )
    )

    if not token:
        raise RuntimeError(
            "AppRole login did not "
            "return a token"
        )

    return token


def ensure_runtime_role(
    root_token,
    role_name,
    additional_policies=None,
):
    policies = [
        role_name,
    ]

    if additional_policies:
        policies.extend(
            additional_policies
        )

    api_request(
        "POST",
        f"auth/approle/role/{role_name}",
        token=root_token,
        payload={
            "bind_secret_id": True,
            "token_policies": policies,
            "secret_id_ttl": "0",
            "secret_id_num_uses": 0,
            "token_ttl": "15m",
            "token_max_ttl": "1h",
            "token_num_uses": 0,
        },
    )

    ok(
        f"{role_name} AppRole configured"
    )


def get_runtime_role_id(
    root_token,
    role_name,
):
    response = api_request(
        "GET",
        f"auth/approle/role/{role_name}/role-id",
        token=root_token,
    )

    role_id = (
        response
        .json()
        .get("data", {})
        .get("role_id")
    )

    if not role_id:
        raise RuntimeError(
            f"OpenBao did not return RoleID for {role_name}"
        )

    return role_id


def generate_runtime_secret_id(
    root_token,
    role_name,
):
    response = api_request(
        "POST",
        f"auth/approle/role/{role_name}/secret-id",
        token=root_token,
        payload={},
    )

    secret_id = (
        response
        .json()
        .get("data", {})
        .get("secret_id")
    )

    if not secret_id:
        raise RuntimeError(
            f"OpenBao did not return SecretID for {role_name}"
        )

    return secret_id


def load_identity_file(identity_file):
    if not identity_file.exists():
        return None

    try:
        with identity_file.open(
            "r",
            encoding="utf-8",
        ) as handle:
            data = json.load(handle)
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return None

    if not isinstance(data, dict):
        return None

    if (
        not data.get("role_id")
        or
        not data.get("secret_id")
    ):
        return None

    return data


def save_identity_file(
    identity_file,
    role_id,
    secret_id,
):
    identity_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with identity_file.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            {
                "role_id": role_id,
                "secret_id": secret_id,
            },
            handle,
            indent=2,
        )
        handle.write("\n")

    os.chmod(
        identity_file,
        0o600,
    )


def ensure_runtime_identity(
    root_token,
    role_name,
    identity_file,
):
    role_id = get_runtime_role_id(
        root_token,
        role_name,
    )

    existing = load_identity_file(
        identity_file
    )

    if (
        existing
        and
        existing.get("role_id") == role_id
    ):
        try:
            login_approle(
                existing["role_id"],
                existing["secret_id"],
            )
            ok(
                f"Existing {role_name} identity is valid"
            )
            return
        except RuntimeError:
            info(
                f"Stored {role_name} identity is invalid; replacing it"
            )

    secret_id = generate_runtime_secret_id(
        root_token,
        role_name,
    )

    login_approle(
        role_id,
        secret_id,
    )

    save_identity_file(
        identity_file,
        role_id,
        secret_id,
    )

    ok(
        f"{role_name} machine identity created and validated"
    )


def reconcile_runtime_role(root_token, role_name):
    """Reconcile one runtime policy, AppRole, and machine identity."""
    if role_name not in RUNTIME_ROLES:
        raise RuntimeError(f"Unknown OpenBao runtime role: {role_name}")

    spec = RUNTIME_ROLES[role_name]
    ensure_runtime_policy(
        root_token,
        role_name,
        spec["policy_file"],
    )
    ensure_runtime_role(
        root_token,
        role_name,
        spec.get("additional_policies", []),
    )
    ensure_runtime_identity(
        root_token,
        role_name,
        spec["identity_file"],
    )


def validate_runtime_identities(optional_roles=()):
    for role_name, spec in RUNTIME_ROLES.items():
        identity = load_identity_file(
            spec["identity_file"]
        )

        if not identity:
            if role_name in optional_roles:
                info(
                    f"{role_name} identity is not bootstrapped on this "
                    "existing OpenBao instance"
                )
                continue
            raise RuntimeError(
                f"{role_name} machine identity is missing. "
                "This OpenBao instance predates the runtime-role bootstrap; "
                "bootstrap it with privileged access or use a fresh test deployment."
            )

        login_approle(
            identity["role_id"],
            identity["secret_id"],
        )

        ok(
            f"{role_name} authentication successful"
        )


# ===========================================================================
# Local machine identity
# ===========================================================================

def load_approle_identity():
    if not APPROLE_STATE_FILE.exists():
        return None

    with APPROLE_STATE_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:

        data = json.load(
            handle
        )

    if not isinstance(
        data,
        dict,
    ):
        return None

    if (
        not data.get("role_id")
        or
        not data.get("secret_id")
    ):
        return None

    return data


def save_approle_identity(
    role_id,
    secret_id,
):
    APPROLE_STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "role_id": role_id,
        "secret_id": secret_id,
    }

    with APPROLE_STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            data,
            handle,
            indent=2,
        )

        handle.write(
            "\n"
        )

    os.chmod(
        APPROLE_STATE_FILE,
        0o600,
    )

    ok(
        "OpenBao machine identity "
        "stored in "
        f"{APPROLE_STATE_FILE.relative_to(ROOT)}"
    )


def ensure_machine_identity(
    root_token,
):
    role_id = get_role_id(
        root_token
    )

    existing = (
        load_approle_identity()
    )

    if (
        existing
        and
        existing.get("role_id")
        == role_id
    ):
        try:
            token = login_approle(
                existing["role_id"],
                existing["secret_id"],
            )

            ok(
                "Existing atlas-deployer "
                "machine identity is valid"
            )

            return token

        except RuntimeError:
            info(
                "Stored AppRole identity "
                "is no longer valid; "
                "replacing it"
            )

    secret_id = generate_secret_id(
        root_token
    )

    token = login_approle(
        role_id,
        secret_id,
    )

    save_approle_identity(
        role_id,
        secret_id,
    )

    ok(
        "atlas-deployer AppRole "
        "login validated"
    )

    return token


# ===========================================================================
# OpenBao bootstrap
# ===========================================================================

def bootstrap_openbao(root_token):
    header(
        "OPENBAO BOOTSTRAP"
    )

    ensure_atlas_kv(
        root_token
    )

    ensure_atlas_policy(
        root_token
    )

    ensure_operator_policy(
        root_token
    )

    ensure_runtime_policy(
        root_token,
        "device-credentials-read",
        DEVICE_CREDENTIALS_POLICY_FILE,
    )

    # AppRole MUST exist before creating any roles.
    ensure_approle_auth(
        root_token
    )

    ensure_atlas_deployer_role(
        root_token
    )

    app_token = ensure_machine_identity(
        root_token
    )

    ensure_operator_role(
        root_token
    )

    create_operator_identity(
        root_token
    )

    for role_name in RUNTIME_ROLES:
        reconcile_runtime_role(root_token, role_name)

    ok(
        "OpenBao bootstrap completed"
    )

    return app_token


# ===========================================================================
# Final output
# ===========================================================================

def print_result(status):
    header(
        "OPENBAO READY"
    )

    print(
        f"Initialized: "
        f"{status['initialized']}"
    )

    print(
        f"Sealed:      "
        f"{status['sealed']}"
    )

    print(
        f"Shares:      "
        f"{status['n']}"
    )

    print(
        f"Threshold:   "
        f"{status['t']}"
    )


# ===========================================================================
# Main
# ===========================================================================

def main():
    try:
        header(
            "ATLAS OPENBAO DEPLOY"
        )

        check_docker()

        start_openbao()

        status = wait_for_openbao()

        root_token = None
        initial_keys = None
        app_token = None

        if not status["initialized"]:
            (
                root_token,
                initial_keys,
            ) = initialize_openbao()

            # When deploy_openbao.py is launched by deploy_atlas.py,
            # pass the fresh-install keys back through an inherited pipe.
            # They remain only in process memory and are never persisted.
            send_initial_keys_to_parent(
                initial_keys
            )

            status = get_seal_status()

        if initial_keys:
            status = unseal_with_keys(
                initial_keys[
                    :UNSEAL_THRESHOLD
                ]
            )

        else:
            status = unseal_openbao(
                status
            )

        # Integrated Raft storage can remain briefly read-only immediately
        # after a fresh unseal while the node elects a leader. Wait for a
        # leader before any audited/bootstrap API requests are attempted.
        wait_for_raft_leader()

        if root_token:
            app_token = bootstrap_openbao(
                root_token
            )

            revoke_initial_root_token(
                root_token
            )

            root_token = None

            identity = load_approle_identity()

            app_token = login_approle(
                identity["role_id"],
                identity["secret_id"],
            )

            ok(
                "AppRole authentication verified "
                "after root token revocation"
            )

            validate_runtime_identities()

        else:
            identity = (
                load_approle_identity()
            )

            if identity:
                app_token = login_approle(
                    identity["role_id"],
                    identity["secret_id"],
                )

                ok(
                    "atlas-deployer "
                    "authentication successful"
                )

                validate_runtime_identities(
                    optional_roles={"atlas-backend"},
                )

            else:
                info(
                    "OpenBao is already initialized, "
                    "but no local Atlas AppRole "
                    "identity exists."
                )

        print_result(
            status
        )

        if app_token:
            ok(
                "Atlas OpenBao machine "
                "authentication ready"
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
