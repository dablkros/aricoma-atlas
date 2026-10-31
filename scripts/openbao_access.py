#!/usr/bin/env python3

import getpass
import sys
from pathlib import Path


ROOT = Path(
    __file__
).resolve().parent.parent

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
from atlas.auth import normalize_username  # noqa: E402
from scripts import deploy_openbao as openbao_deployment  # noqa: E402


OPENBAO_URL = (
    "http://127.0.0.1:18200"
)

DEPLOYER_IDENTITY = (
    ROOT
    / ".runtime"
    / "openbao-approle.json"
)

OPERATOR_IDENTITY = (
    ROOT
    / ".runtime"
    / "openbao-operator.json"
)


def header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def ok(message):
    print(
        f"[OK] {message}"
    )


def error(message):
    print(
        f"[ERROR] {message}"
    )


def ensure_openbao_ready(client):
    status = client.seal_status()

    if not status.get(
        "initialized"
    ):
        raise OpenBaoError(
            "OpenBao is not initialized"
        )

    if status.get(
        "sealed"
    ):
        raise OpenBaoError(
            "OpenBao is sealed. "
            "Unseal it first."
        )

    return status


def format_ttl(seconds):
    if seconds is None:
        return "unknown"

    try:
        seconds = int(
            seconds
        )
    except (
        TypeError,
        ValueError,
    ):
        return str(seconds)

    if seconds % 3600 == 0:
        return (
            f"{seconds // 3600}h"
        )

    if seconds % 60 == 0:
        return (
            f"{seconds // 60}m"
        )

    return (
        f"{seconds}s"
    )


# ===========================================================================
# Deployer access
# ===========================================================================

def generate_deployer_token(client):
    header(
        "ATLAS DEPLOYER TOKEN"
    )

    ensure_openbao_ready(
        client
    )

    token = client.login_from_identity(
        DEPLOYER_IDENTITY
    )

    info = client.lookup_self(
        token
    )

    print()
    print(
        "Token:"
    )
    print(
        token
    )
    print()
    print(
        "TTL:      "
        f"{format_ttl(info.get('ttl'))}"
    )
    print(
        "Policies: "
        f"{', '.join(info.get('policies', []))}"
    )
    print()

    ok(
        "Deployer token generated"
    )


# ===========================================================================
# Root access
# ===========================================================================

def select_root_ttl():
    print()
    print(
        "Temporary root lifetime:"
    )
    print()
    print(
        "1. 15 minutes"
    )
    print(
        "2. 1 hour"
    )
    print(
        "3. 4 hours"
    )
    print(
        "4. Cancel"
    )
    print()

    choice = input(
        "Select: "
    ).strip()

    values = {
        "1": "15m",
        "2": "1h",
        "3": "4h",
    }

    return values.get(
        choice
    )


def run_controlled_root_action(client, root_token, action):
    """Run one privileged operation and always attempt token revocation."""
    try:
        return action(root_token)
    finally:
        client.revoke_self(root_token)


def generate_temporary_root(client, action=None, requested_ttl=None):
    header(
        "TEMPORARY ROOT ACCESS"
    )

    status = ensure_openbao_ready(
        client
    )

    ttl = requested_ttl or select_root_ttl()

    if not ttl:
        print(
            "Cancelled."
        )
        return False

    operator_token = (
        client.login_from_identity(
            OPERATOR_IDENTITY
        )
    )

    current = (
        client.root_generation_status(
            operator_token
        )
    )

    if current.get(
        "started"
    ):
        print()
        print(
            "A root generation attempt "
            "is already active."
        )

        answer = input(
            "Cancel it and start a new one? "
            "[y/N]: "
        ).strip().lower()

        if answer != "y":
            print(
                "Cancelled."
            )
            return False

        client.cancel_root_generation(
            operator_token
        )

        ok(
            "Previous root generation "
            "attempt cancelled"
        )

    attempt_active = False

    try:
        attempt = (
            client.start_root_generation(
                operator_token
            )
        )

        attempt_active = True

        nonce = attempt.get(
            "nonce"
        )

        otp = attempt.get(
            "otp"
        )

        required = int(
            attempt.get(
                "required",
                status.get(
                    "t",
                    0,
                ),
            )
        )

        if required <= 0:
            raise OpenBaoError(
                "Invalid root generation "
                "threshold"
            )

        print()
        print(
            "Root generation requires "
            f"{required} of "
            f"{status.get('n')} "
            "unseal shares."
        )
        print()

        state = attempt

        while not state.get(
            "complete"
        ):
            progress = int(
                state.get(
                    "progress",
                    0,
                )
            )

            key = getpass.getpass(
                f"Unseal key "
                f"{progress + 1}/"
                f"{required}: "
            )

            if not key:
                print(
                    "[WARN] Empty key ignored."
                )
                continue

            try:
                state = (
                    client.submit_root_share(
                        operator_token,
                        nonce,
                        key,
                    )
                )

            except OpenBaoError as exc:
                print(
                    f"[WARN] {exc}"
                )
                continue

            if not state.get(
                "complete"
            ):
                print(
                    "[INFO] Root generation "
                    "progress: "
                    f"{state.get('progress')}/"
                    f"{required}"
                )

        attempt_active = False

        encoded_token = (
            state.get(
                "encoded_token"
            )
        )

        generated_root = (
            client.decode_root_token(
                encoded_token,
                otp,
            )
        )

        temporary = (
            client.exchange_generated_root(
                generated_root,
                ttl,
            )
        )

    except KeyboardInterrupt:
        print()

        if attempt_active:
            try:
                client.cancel_root_generation(
                    operator_token
                )

                print(
                    "[OK] Root generation "
                    "attempt cancelled"
                )

            except Exception:
                print(
                    "[WARN] Unable to cancel "
                    "root generation attempt"
                )

        print(
            "Cancelled."
        )
        return False

    except Exception:
        if attempt_active:
            try:
                client.cancel_root_generation(
                    operator_token
                )
            except Exception:
                pass

        raise

    root_token = temporary[
        "token"
    ]

    if action is not None:
        run_controlled_root_action(client, root_token, action)

        ok(
            "Temporary root token revoked after the controlled operation"
        )
        return True

    root_info = client.lookup_self(
        root_token
    )

    print()
    print(
        "=" * 78
    )
    print(
        "TEMPORARY ROOT TOKEN"
    )
    print(
        "=" * 78
    )
    print()
    print(
        root_token
    )
    print()
    print(
        "TTL:              "
        f"{format_ttl(root_info.get('ttl'))}"
    )
    print(
        "Explicit max TTL: "
        f"{format_ttl(root_info.get('explicit_max_ttl'))}"
    )
    print(
        "Policies:         "
        f"{', '.join(root_info.get('policies', []))}"
    )
    print(
        "Renewable:        "
        f"{root_info.get('renewable')}"
    )
    print()

    ok(
        "Quorum-generated bootstrap "
        "root token was revoked"
    )

    print()
    answer = input(
        "Press ENTER to revoke this "
        "temporary root token now, "
        "or type 'keep' to leave it "
        "active until its TTL expires: "
    ).strip().lower()

    if answer == "keep":
        print()
        print(
            f"Token remains active for "
            f"up to {ttl}."
        )

        return

    client.revoke_self(
        root_token
    )

    ok(
        "Temporary root token revoked"
    )


def reconcile_backend_policy(client):
    """Reconcile Atlas backend runtime roles without exposing a root token."""

    def reconcile(root_token):
        openbao_deployment.reconcile_runtime_role(
            root_token,
            "atlas-backend",
        )
        openbao_deployment.reconcile_runtime_role(
            root_token,
            "atlas-monitoring-provisioner",
        )
        openbao_deployment.reconcile_runtime_role(
            root_token,
            "atlas-credential-writer",
        )
        openbao_deployment.reconcile_runtime_role(
            root_token,
            "atlas-vpn-profile-writer",
        )
        openbao_deployment.reconcile_runtime_role(
            root_token,
            "atlas-vpn-worker",
        )

    applied = generate_temporary_root(
        client,
        action=reconcile,
        requested_ttl="15m",
    )
    if not applied:
        print("[INFO] Atlas runtime role reconcile was cancelled")
        return False
    ok("Atlas backend policies and AppRoles reconciled")
    return True


def show_admin_recovery_password(client):
    """Read one recovery password through a quorum-controlled root token."""
    header("ATLAS ADMINISTRATOR RECOVERY PASSWORD")
    username = normalize_username(input("Administrator username [admin]: ").strip() or "admin")

    def show(root_token):
        try:
            record = client.kv_read(
                root_token,
                f"auth/recovery/users/{username}",
                mount="atlas",
            )
        except OpenBaoNotFound:
            raise OpenBaoError(
                "No readable recovery password is stored for this administrator"
            ) from None
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("username") != username
            or not isinstance(record.get("password"), str)
            or not record["password"]
        ):
            raise OpenBaoError("Atlas administrator recovery record is invalid")
        print()
        print("=" * 78)
        print("RECOVERED ATLAS ADMINISTRATOR CREDENTIAL")
        print("=" * 78)
        print(f"Username: {username}")
        print(f"Password: {record['password']}")
        print()
        print("[WARN] This terminal now contains the plaintext administrator password")

    applied = generate_temporary_root(
        client,
        action=show,
        requested_ttl="15m",
    )
    if not applied:
        print("[INFO] Administrator password recovery was cancelled")
        return False
    return True


# ===========================================================================
# Revoke
# ===========================================================================

def revoke_token(client):
    header(
        "REVOKE TOKEN"
    )

    ensure_openbao_ready(
        client
    )

    token = getpass.getpass(
        "Token to revoke: "
    )

    if not token:
        print(
            "Cancelled."
        )
        return

    client.revoke_self(
        token
    )

    ok(
        "Token revoked"
    )


# ===========================================================================
# Inspect
# ===========================================================================

def inspect_token(client):
    header(
        "INSPECT TOKEN"
    )

    ensure_openbao_ready(
        client
    )

    token = getpass.getpass(
        "Token: "
    )

    if not token:
        print(
            "Cancelled."
        )
        return

    info = client.lookup_self(
        token
    )

    print()
    print(
        f"Display name:     "
        f"{info.get('display_name')}"
    )
    print(
        f"TTL:              "
        f"{format_ttl(info.get('ttl'))}"
    )
    print(
        f"Explicit max TTL: "
        f"{format_ttl(info.get('explicit_max_ttl'))}"
    )
    print(
        f"Renewable:        "
        f"{info.get('renewable')}"
    )
    print(
        f"Orphan:           "
        f"{info.get('orphan')}"
    )
    print(
        f"Policies:         "
        f"{', '.join(info.get('policies', []))}"
    )

    if info.get(
        "expire_time"
    ):
        print(
            f"Expires:          "
            f"{info.get('expire_time')}"
        )


# ===========================================================================
# Menu
# ===========================================================================

def menu():
    client = OpenBaoClient(
        OPENBAO_URL
    )

    while True:
        header(
            "OPENBAO ACCESS"
        )

        print(
            "1. Generate Atlas deployer token"
        )
        print(
            "2. Reconcile Atlas backend roles"
        )
        print(
            "3. Show Atlas administrator recovery password"
        )
        print(
            "4. Generate temporary root token"
        )
        print(
            "5. Revoke token"
        )
        print(
            "6. Inspect token"
        )
        print(
            "7. Exit"
        )
        print()

        choice = input(
            "Select: "
        ).strip()

        try:
            if choice == "1":
                generate_deployer_token(
                    client
                )

            elif choice == "2":
                reconcile_backend_policy(
                    client
                )

            elif choice == "3":
                show_admin_recovery_password(
                    client
                )

            elif choice == "4":
                generate_temporary_root(
                    client
                )

            elif choice == "5":
                revoke_token(
                    client
                )

            elif choice == "6":
                inspect_token(
                    client
                )

            elif choice == "7":
                return

            else:
                print(
                    "[ERROR] Invalid selection"
                )

        except (OpenBaoError, ValueError) as exc:
            error(
                str(exc)
            )

        except KeyboardInterrupt:
            print()
            print(
                "Cancelled."
            )

        print()
        input(
            "Press ENTER to continue..."
        )


def main():
    try:
        if sys.argv[1:] == ["--show-admin-password"]:
            client = OpenBaoClient(OPENBAO_URL)
            if not show_admin_recovery_password(client):
                raise SystemExit(1)
            return
        if sys.argv[1:] == ["--reconcile-runtime-roles"]:
            client = OpenBaoClient(OPENBAO_URL)
            if not reconcile_backend_policy(client):
                raise SystemExit(1)
            return
        if sys.argv[1:]:
            raise SystemExit(
                "Usage: openbao_access.py "
                "[--show-admin-password|--reconcile-runtime-roles]"
            )
        menu()

    except KeyboardInterrupt:
        print()
        raise SystemExit(
            0
        )

    except (OpenBaoError, ValueError) as exc:
        raise SystemExit(f"[ERROR] {exc}") from None

if __name__ == "__main__":
    main()
