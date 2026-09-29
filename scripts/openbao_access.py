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
)


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


def generate_temporary_root(client):
    header(
        "TEMPORARY ROOT ACCESS"
    )

    status = ensure_openbao_ready(
        client
    )

    ttl = select_root_ttl()

    if not ttl:
        print(
            "Cancelled."
        )
        return

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
            return

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
        return

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
            "2. Generate temporary root token"
        )
        print(
            "3. Revoke token"
        )
        print(
            "4. Inspect token"
        )
        print(
            "5. Exit"
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
                generate_temporary_root(
                    client
                )

            elif choice == "3":
                revoke_token(
                    client
                )

            elif choice == "4":
                inspect_token(
                    client
                )

            elif choice == "5":
                return

            else:
                print(
                    "[ERROR] Invalid selection"
                )

        except OpenBaoError as exc:
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
        menu()

    except KeyboardInterrupt:
        print()
        raise SystemExit(
            0
        )


if __name__ == "__main__":
    main()
