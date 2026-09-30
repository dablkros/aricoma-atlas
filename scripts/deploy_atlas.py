#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from atlas.openbao_client import (  # noqa: E402
    OpenBaoClient,
    OpenBaoError,
)
from atlas.proxy import load_config as load_proxy_config  # noqa: E402


SCRIPTS_DIR = ROOT / "scripts"

OPENBAO_DEPLOY_SCRIPT = (
    SCRIPTS_DIR
    / "deploy_openbao.py"
)

NETBOX_DEPLOY_SCRIPT = (
    SCRIPTS_DIR
    / "deploy_netbox.py"
)
OXIDIZED_DEPLOY_SCRIPT = SCRIPTS_DIR / "deploy_oxidized.py"
PROXY_DEPLOY_SCRIPT = SCRIPTS_DIR / "deploy_proxy.py"

OPENBAO_IDENTITY_FILE = (
    ROOT
    / ".runtime"
    / "openbao-approle.json"
)

OPENBAO_URL = os.environ.get(
    "OPENBAO_URL",
    "http://127.0.0.1:18200",
)


# ===========================================================================
# CLI
# ===========================================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Deploy the Aricoma Atlas stack"
        )
    )

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help=(
            "Prepare NetBox, Oxidized and Nginx runtimes and secrets "
            "without starting their containers. "
            "OpenBao is still started because it "
            "is the secrets source of truth."
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
# Script execution
# ===========================================================================


def require_script(path):
    if not path.exists():
        raise RuntimeError(
            f"Required deployment script not found: "
            f"{path}"
        )


def run_script(
    path,
    args=None,
    env=None,
    pass_fds=(),
):
    require_script(path)

    command = [
        sys.executable,
        str(path),
    ]

    if args:
        command.extend(args)

    process_env = os.environ.copy()

    if env:
        process_env.update(
            env
        )

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=process_env,
        pass_fds=pass_fds,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Deployment stage failed: "
            f"{path.name} "
            f"(exit code {result.returncode})"
        )


# ===========================================================================
# OpenBao readiness gate
# ===========================================================================


def verify_openbao_ready():
    header(
        "VERIFY OPENBAO"
    )

    if not OPENBAO_IDENTITY_FILE.exists():
        raise RuntimeError(
            "OpenBao is running but the Atlas "
            "deployer identity is missing: "
            f"{OPENBAO_IDENTITY_FILE}"
        )

    client = OpenBaoClient(
        OPENBAO_URL
    )

    try:
        status = client.seal_status()
    except OpenBaoError as exc:
        raise RuntimeError(
            "OpenBao API is not ready: "
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
            "OpenBao is still sealed"
        )

    token = None

    try:
        token = client.login_from_identity(
            OPENBAO_IDENTITY_FILE
        )
    except OpenBaoError as exc:
        raise RuntimeError(
            "Atlas deployer cannot authenticate "
            "to OpenBao: "
            f"{exc}"
        ) from exc

    if not token:
        raise RuntimeError(
            "OpenBao AppRole login returned "
            "no token"
        )

    # This token exists only for the readiness check.
    # Revoke it immediately so deploy_atlas.py does not
    # leave an unnecessary short-lived service token behind.
    try:
        client.revoke_self(
            token
        )
    except OpenBaoError as exc:
        raise RuntimeError(
            "OpenBao authentication succeeded, "
            "but the readiness token could not "
            "be revoked: "
            f"{exc}"
        ) from exc

    ok(
        "OpenBao is initialized, unsealed "
        "and Atlas AppRole authentication works"
    )


# ===========================================================================
# Deployment stages
# ===========================================================================


def deploy_openbao():
    header(
        "ATLAS STAGE 1/4 - OPENBAO"
    )

    read_fd, write_fd = os.pipe()

    try:
        try:
            run_script(
                OPENBAO_DEPLOY_SCRIPT,
                env={
                    "ATLAS_UNSEAL_KEYS_FD":
                        str(write_fd),
                },
                pass_fds=(write_fd,),
            )
        finally:
            os.close(
                write_fd
            )

        raw = b""

        while True:
            chunk = os.read(
                read_fd,
                65536,
            )

            if not chunk:
                break

            raw += chunk

    finally:
        os.close(
            read_fd
        )

    initial_unseal = None

    if raw:
        try:
            initial_unseal = json.loads(
                raw.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError(
                "OpenBao returned invalid "
                "fresh-install key data"
            ) from exc

        keys = initial_unseal.get(
            "keys"
        )

        if (
            not isinstance(keys, list)
            or
            len(keys) != int(
                initial_unseal.get(
                    "shares",
                    0,
                )
            )
        ):
            raise RuntimeError(
                "OpenBao returned incomplete "
                "fresh-install unseal keys"
            )

    verify_openbao_ready()

    ok(
        "OpenBao stage completed"
    )

    return initial_unseal


def deploy_netbox(args):
    header(
        "ATLAS STAGE 2/4 - NETBOX"
    )

    netbox_args = []

    if args.prepare_only:
        netbox_args.append(
            "--prepare-only"
        )

    if args.verbose:
        netbox_args.append(
            "--verbose"
        )

    run_script(
        NETBOX_DEPLOY_SCRIPT,
        netbox_args,
    )

    ok(
        "NetBox stage completed"
    )


def deploy_oxidized(args):
    header("ATLAS STAGE 3/4 - OXIDIZED")
    run_script(OXIDIZED_DEPLOY_SCRIPT, ["--prepare-only"] if args.prepare_only else [])


def deploy_proxy(args):
    header("ATLAS STAGE 4/4 - NGINX HTTPS INGRESS")
    run_script(PROXY_DEPLOY_SCRIPT, ["--prepare-only"] if args.prepare_only else [])


# ===========================================================================
# Final output
# ===========================================================================


def print_initial_unseal_keys(initial_unseal):
    if not initial_unseal:
        return

    keys = initial_unseal[
        "keys"
    ]

    shares = initial_unseal[
        "shares"
    ]

    threshold = initial_unseal[
        "threshold"
    ]

    header(
        "IMPORTANT: OPENBAO UNSEAL KEYS"
    )

    print(
        "Store these keys outside this Atlas server."
    )
    print(
        "They are NOT stored by Atlas."
    )
    print()

    for index, key in enumerate(
        keys,
        start=1,
    ):
        print(
            f"Unseal Key {index}: {key}"
        )

    print()
    print(
        f"Threshold: {threshold} of {shares}"
    )
    print()
    print(
        "This is the final display of these keys "
        "by this deployment process."
    )


def print_result(args):
    header(
        "ARICOMA ATLAS READY"
    )

    print(
        "OpenBao: ready"
    )

    if args.prepare_only:
        print(
            "NetBox:  prepared only"
        )
    else:
        print(
            "NetBox:  ready"
        )

    print()
    print(
        "Secrets source of truth: OpenBao"
    )
    print("Oxidized: prepared only" if args.prepare_only else "Oxidized: ready (inventory initially empty)")
    print("Nginx: prepared only" if args.prepare_only else "Nginx: ready")


# ===========================================================================
# Main
# ===========================================================================


def main():
    args = parse_args()

    try:
        # Validate customer DNS, subnets and CA certificates before any deployment.
        load_proxy_config()
        header(
            "ARICOMA ATLAS DEPLOY"
        )

        info(
            f"Python interpreter: {sys.executable}"
        )

        initial_unseal = deploy_openbao()

        # NetBox must never start unless the OpenBao
        # deployment and readiness gate completed.
        deploy_netbox(
            args
        )
        deploy_oxidized(args)
        deploy_proxy(args)

        print_result(
            args
        )

        # On a fresh install only, repeat the unseal keys at the very end
        # for the administrator. The keys existed only in process memory.
        print_initial_unseal_keys(
            initial_unseal
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
