#!/usr/bin/env python3
"""Deploy Zabbix 7.0 LTS and bootstrap the restricted Atlas API identity."""

import argparse
import copy
import ipaddress
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import (  # noqa: E402
    ZABBIX_NETWORK,
    connect_openbao,
    ensure_network,
    optional_secret,
    wait_healthy,
    write_private,
    write_yaml,
)
from atlas.integrations.zabbix import ZabbixAPIError, ZabbixClient  # noqa: E402
from atlas.zabbix_config import load_zabbix_config  # noqa: E402


CONFIG_FILE = ROOT / "deployment/zabbix.yaml"
IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*:[A-Za-z0-9][A-Za-z0-9._-]*")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]*")
API_METHODS = [
    "host.get",
    "host.create",
    "host.update",
    "hostgroup.get",
    "template.get",
    "item.get",
    "problem.get",
    "history.get",
    "trend.get",
]


def load_config(path=CONFIG_FILE):
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read Zabbix configuration: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Zabbix configuration must be an object")
    config = copy.deepcopy(raw)
    try:
        zabbix = config["zabbix"]
        runtime = config["runtime"]
        network = config["network"]
    except KeyError as exc:
        raise ValueError(f"Missing Zabbix configuration section: {exc.args[0]}") from None
    mapping = load_zabbix_config()
    if zabbix.get("version") != mapping["zabbix_version"]:
        raise ValueError("Zabbix deployment and mapping versions differ")
    for field in ("server_image", "web_image", "postgres_image"):
        value = zabbix.get(field)
        if not isinstance(value, str) or not IMAGE.fullmatch(value) or "latest" in value:
            raise ValueError(f"zabbix.{field} must use an explicit non-latest tag")
    if any(zabbix["version"] not in zabbix[field] for field in ("server_image", "web_image")):
        raise ValueError("Zabbix server and web images must match zabbix.version")
    server_name = zabbix.get("server_name")
    if not isinstance(server_name, str) or not server_name.strip() or len(server_name) > 128:
        raise ValueError("zabbix.server_name is invalid")
    directory = Path(str(runtime.get("directory", "")))
    if directory.is_absolute() or ".." in directory.parts or directory.parts[:1] != (".runtime",):
        raise ValueError("runtime.directory must be below .runtime")
    if not isinstance(runtime.get("compose_project"), str) or not PROJECT.fullmatch(runtime["compose_project"]):
        raise ValueError("runtime.compose_project is invalid")
    timeout = runtime.get("startup_timeout")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 30:
        raise ValueError("runtime.startup_timeout must be at least 30")
    try:
        address = ipaddress.ip_address(network["listen_address"])
    except (KeyError, ValueError) as exc:
        raise ValueError("network.listen_address is invalid") from exc
    if not address.is_loopback:
        raise ValueError("Zabbix web may only publish on loopback")
    network["listen_address"] = str(address)
    for field in ("host_port", "container_port"):
        value = network.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(f"network.{field} is invalid")
    return config


def runtime_path(config, root=ROOT):
    return Path(root) / config["runtime"]["directory"]


def ensure_database_secret(client, token):
    existing = optional_secret(client, token, "zabbix/database")
    if existing is None:
        existing = {
            "username": "zabbix",
            "database": "zabbix",
            "password": secrets.token_urlsafe(48),
        }
        client.kv_write(token, "zabbix/database", existing)
    if (
        not isinstance(existing, dict)
        or existing.get("username") != "zabbix"
        or existing.get("database") != "zabbix"
        or not isinstance(existing.get("password"), str)
        or len(existing["password"]) < 32
        or any(character in existing["password"] for character in "\r\n\x00")
    ):
        raise ValueError("Invalid OpenBao atlas/zabbix/database secret")
    return existing


def prepare_runtime(config, database_secret, root=ROOT):
    root = Path(root).resolve()
    runtime = runtime_path(config, root)
    runtime_base = root / ".runtime"
    if runtime_base.is_symlink() or runtime.is_symlink():
        raise RuntimeError("Zabbix runtime cannot be a symbolic link")
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)
    secret_dir = runtime / "secrets"
    if secret_dir.is_symlink():
        raise RuntimeError("Zabbix secret directory cannot be a symbolic link")
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(secret_dir, 0o700)
    # Compose implements file-backed secrets as bind mounts and cannot remap
    # their ownership.  The Zabbix images run as UID 1997, so host-only 0600
    # files are unreadable in those containers.  Keep both parent directories
    # private (0700) and expose only read-only files through the per-service
    # Compose secret grants.
    write_private(
        secret_dir / "postgres-user",
        database_secret["username"] + "\n",
        mode=0o444,
    )
    write_private(
        secret_dir / "postgres-password",
        database_secret["password"] + "\n",
        mode=0o444,
    )

    zabbix = config["zabbix"]
    network = config["network"]
    common_database = {
        "DB_SERVER_HOST": "database",
        "POSTGRES_DB": database_secret["database"],
        "POSTGRES_USER_FILE": "/run/secrets/postgres_user",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
    }
    compose_file = runtime / "docker-compose.yml"
    write_yaml(
        compose_file,
        {
            "services": {
                "database": {
                    "image": zabbix["postgres_image"],
                    "restart": "unless-stopped",
                    "environment": {
                        "POSTGRES_DB": database_secret["database"],
                        "POSTGRES_USER_FILE": "/run/secrets/postgres_user",
                        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
                    },
                    "secrets": ["postgres_user", "postgres_password"],
                    "volumes": ["database_data:/var/lib/postgresql/data"],
                    "networks": ["database"],
                    "healthcheck": {
                        "test": ["CMD-SHELL", "pg_isready -U zabbix -d zabbix"],
                        "interval": "5s",
                        "timeout": "5s",
                        "retries": 30,
                        "start_period": "10s",
                    },
                },
                "server": {
                    "image": zabbix["server_image"],
                    "restart": "unless-stopped",
                    "init": True,
                    "environment": common_database,
                    "secrets": ["postgres_user", "postgres_password"],
                    "depends_on": {"database": {"condition": "service_healthy"}},
                    "networks": {
                        "database": {},
                        "api": {"aliases": ["atlas-zabbix-server"]},
                    },
                    "cap_add": ["NET_RAW"],
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "healthcheck": {
                        "test": ["CMD-SHELL", "pidof zabbix_server >/dev/null"],
                        "interval": "10s",
                        "timeout": "5s",
                        "retries": 30,
                        "start_period": "30s",
                    },
                },
                "web": {
                    "image": zabbix["web_image"],
                    "restart": "unless-stopped",
                    "environment": {
                        **common_database,
                        "ZBX_SERVER_HOST": "server",
                        "ZBX_SERVER_NAME": zabbix["server_name"],
                        "PHP_TZ": "Europe/Bratislava",
                    },
                    "secrets": ["postgres_user", "postgres_password"],
                    "depends_on": {
                        "database": {"condition": "service_healthy"},
                        "server": {"condition": "service_healthy"},
                    },
                    "ports": [
                        f"{network['listen_address']}:{network['host_port']}:{network['container_port']}"
                    ],
                    "networks": {
                        "database": {},
                        "api": {"aliases": ["atlas-zabbix-web"]},
                    },
                    "cap_drop": ["ALL"],
                    "security_opt": ["no-new-privileges:true"],
                    "healthcheck": {
                        "test": [
                            "CMD-SHELL",
                            "curl -fsS http://127.0.0.1:8080/ping >/dev/null",
                        ],
                        "interval": "10s",
                        "timeout": "5s",
                        "retries": 30,
                        "start_period": "30s",
                    },
                },
            },
            "networks": {
                "database": {"internal": True},
                "api": {"external": True, "name": ZABBIX_NETWORK},
            },
            "volumes": {
                "database_data": {"name": "atlas-zabbix-database"},
            },
            "secrets": {
                "postgres_user": {"file": str(secret_dir / "postgres-user")},
                "postgres_password": {"file": str(secret_dir / "postgres-password")},
            },
        },
    )
    os.chmod(compose_file, 0o600)
    return compose_file


def compose_step(file, project, label, *args):
    """Run one Zabbix Compose step with bounded, actionable diagnostics."""
    result = subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(file), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return

    # Compose receives only paths to secret files, never their values.  Its
    # stderr is therefore safe to retain and identifies image, mount, network,
    # dependency-health, and container-start failures that the shared runner
    # intentionally suppresses.
    detail = (result.stderr or result.stdout).strip()
    if detail:
        detail = "\n".join(detail.splitlines()[-20:])[-4000:]
        raise RuntimeError(
            f"Zabbix {label} failed (exit {result.returncode}):\n{detail}"
        )
    raise RuntimeError(f"Zabbix {label} failed (exit {result.returncode})")


def api_url(config):
    network = config["network"]
    return f"http://{network['listen_address']}:{network['host_port']}/api_jsonrpc.php"


def wait_api(config):
    client = ZabbixClient(api_url(config), None, 3, 10)
    deadline = time.monotonic() + config["runtime"]["startup_timeout"]
    while time.monotonic() < deadline:
        try:
            version = client.api_version()
            if version == config["zabbix"]["version"]:
                return
            raise RuntimeError(
                f"Zabbix API version {version} does not match {config['zabbix']['version']}"
            )
        except ZabbixAPIError as exc:
            if exc.code not in {"connection_failed", "http_error", "invalid_response"}:
                raise
        time.sleep(2)
    raise RuntimeError("Zabbix API did not become ready")


def _single(result, field):
    values = result.get(field) if isinstance(result, dict) else None
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("Zabbix bootstrap returned an invalid identifier")
    return str(values[0])


def _find_one(admin, method, object_field, value, output):
    result = admin.call(method, {"output": output, "filter": {object_field: [value]}})
    if not isinstance(result, list) or len(result) > 1:
        raise RuntimeError(f"Zabbix {method} returned ambiguous state")
    return result[0] if result else None


def _admin_client(config, bao, bao_token):
    unauthenticated = ZabbixClient(api_url(config), None, 3, 15)
    stored = optional_secret(bao, bao_token, "zabbix/admin")
    if stored is None:
        username = "Admin"
        password = "zabbix"
    else:
        username = stored.get("username") if isinstance(stored, dict) else None
        password = stored.get("password") if isinstance(stored, dict) else None
        if username != "Admin" or not isinstance(password, str) or len(password) < 32:
            raise RuntimeError("Invalid OpenBao atlas/zabbix/admin secret")

    def login(candidate):
        try:
            value = unauthenticated.call(
                "user.login",
                {"username": username, "password": candidate},
                authenticated=False,
            )
        except ZabbixAPIError as exc:
            if exc.code == "authentication_failed":
                return None
            raise RuntimeError("Zabbix bootstrap administrator login failed") from exc
        if not isinstance(value, str) or not value:
            raise RuntimeError("Zabbix user.login returned invalid data")
        return value

    session = login(password)
    using_default = stored is None
    if session is None and stored is not None:
        # Recovery for an interrupted first deployment: the desired password
        # was committed to OpenBao before Zabbix accepted the password update.
        session = login("zabbix")
        using_default = session is not None
    if session is None:
        raise RuntimeError("Zabbix bootstrap administrator authentication failed")

    admin = ZabbixClient(api_url(config), session, 3, 15, auth_mode="session")
    if using_default:
        user = _find_one(admin, "user.get", "username", "Admin", ["userid", "username"])
        if user is None:
            raise RuntimeError("Default Zabbix Admin user was not found")
        new_password = password if stored is not None else secrets.token_urlsafe(48)
        if stored is None:
            # Persist the desired value first. If the following API call is
            # interrupted, the next run can authenticate with the default and
            # finish applying this already stored password.
            bao.kv_write(
                bao_token,
                "zabbix/admin",
                {"username": "Admin", "password": new_password},
            )
        admin.call("user.update", {"userid": user["userid"], "passwd": new_password})
        try:
            admin.call("user.logout", [])
        except ZabbixAPIError:
            # A password change may invalidate this session immediately.
            pass
        session = login(new_password)
        if session is None:
            raise RuntimeError("Zabbix administrator password rotation failed")
        admin = ZabbixClient(api_url(config), session, 3, 15, auth_mode="session")
    return admin


def _bootstrap_api_identity(config, bao, bao_token, admin):
    existing = optional_secret(bao, bao_token, "zabbix/api")
    existing_token = None
    if existing is not None:
        existing_token = existing.get("token") if isinstance(existing, dict) else None
        if not isinstance(existing_token, str) or not existing_token:
            raise RuntimeError("Invalid OpenBao atlas/zabbix/api secret")

    group_name = load_zabbix_config()["managed_host_group"]
    group = _find_one(admin, "hostgroup.get", "name", group_name, ["groupid", "name"])
    if group is None:
        groupid = _single(admin.call("hostgroup.create", {"name": group_name}), "groupids")
    else:
        groupid = str(group["groupid"])

    role = _find_one(admin, "role.get", "name", "Aricoma Atlas API", ["roleid", "name", "type"])
    if role is None:
        roleid = _single(
            admin.call(
                "role.create",
                {
                    "name": "Aricoma Atlas API",
                    "type": 2,
                    "rules": {"api.access": 1, "api.mode": 1, "api": API_METHODS},
                },
            ),
            "roleids",
        )
    else:
        roleid = str(role["roleid"])
        admin.call(
            "role.update",
            {
                "roleid": roleid,
                "name": "Aricoma Atlas API",
                "type": 2,
                "rules": {"api.access": 1, "api.mode": 1, "api": API_METHODS},
            },
        )

    template_group = _find_one(
        admin,
        "templategroup.get",
        "name",
        "Templates/Network devices",
        ["groupid", "name"],
    )
    if template_group is None:
        raise RuntimeError("Zabbix template group 'Templates/Network devices' is missing")
    user_group = _find_one(
        admin,
        "usergroup.get",
        "name",
        "Aricoma Atlas API",
        ["usrgrpid", "name"],
    )
    if user_group is None:
        usrgrpid = _single(
            admin.call(
                "usergroup.create",
                {
                    "name": "Aricoma Atlas API",
                    "hostgroup_rights": [{"id": groupid, "permission": 3}],
                    "templategroup_rights": [
                        {"id": str(template_group["groupid"]), "permission": 2}
                    ],
                },
            ),
            "usrgrpids",
        )
    else:
        usrgrpid = str(user_group["usrgrpid"])
        admin.call(
            "usergroup.update",
            {
                "usrgrpid": usrgrpid,
                "name": "Aricoma Atlas API",
                "hostgroup_rights": [{"id": groupid, "permission": 3}],
                "templategroup_rights": [
                    {"id": str(template_group["groupid"]), "permission": 2}
                ],
            },
        )

    user = _find_one(admin, "user.get", "username", "atlas-api", ["userid", "username"])
    if user is None:
        userid = _single(
            admin.call(
                "user.create",
                {
                    "username": "atlas-api",
                    "passwd": secrets.token_urlsafe(48),
                    "roleid": roleid,
                    "usrgrps": [{"usrgrpid": usrgrpid}],
                },
            ),
            "userids",
        )
    else:
        userid = str(user["userid"])
        admin.call(
            "user.update",
            {
                "userid": userid,
                "roleid": roleid,
                "usrgrps": [{"usrgrpid": usrgrpid}],
            },
        )

    if existing_token is not None:
        runtime = ZabbixClient(api_url(config), existing_token, 3, 15)
        runtime.host_get({"output": ["hostid"], "limit": 1})
        runtime.template_get({"output": ["templateid"], "limit": 1})
        return

    tokens = admin.call(
        "token.get",
        {
            "output": ["tokenid", "name", "userid"],
            "userids": [userid],
            "filter": {"name": ["Aricoma Atlas backend"]},
        },
    )
    if not isinstance(tokens, list) or len(tokens) > 1:
        raise RuntimeError("Zabbix API token state is ambiguous")
    if tokens:
        tokenid = str(tokens[0]["tokenid"])
    else:
        tokenid = _single(
            admin.call(
                "token.create",
                {"name": "Aricoma Atlas backend", "userid": userid, "status": 0},
            ),
            "tokenids",
        )
    generated = admin.call("token.generate", [tokenid])
    if (
        not isinstance(generated, list)
        or len(generated) != 1
        or not isinstance(generated[0].get("token"), str)
    ):
        raise RuntimeError("Zabbix token.generate returned invalid data")
    api_token = generated[0]["token"]
    runtime = ZabbixClient(api_url(config), api_token, 3, 15)
    runtime.host_get({"output": ["hostid"], "limit": 1})
    runtime.template_get({"output": ["templateid"], "limit": 1})
    bao.kv_write(bao_token, "zabbix/api", {"token": api_token})


def bootstrap_api_identity(config, bao, bao_token):
    admin = _admin_client(config, bao, bao_token)
    try:
        return _bootstrap_api_identity(config, bao, bao_token, admin)
    finally:
        try:
            admin.call("user.logout", [])
        except ZabbixAPIError:
            pass


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    bao, token = connect_openbao()
    try:
        database_secret = ensure_database_secret(bao, token)
        compose_file = prepare_runtime(config, database_secret)
        project = config["runtime"]["compose_project"]
        ensure_network(ZABBIX_NETWORK)
        compose_step(
            compose_file,
            project,
            "Compose validation",
            "config",
            "--quiet",
        )
        if args.prepare_only:
            print("[OK] Zabbix runtime and secrets prepared; no containers started")
            return
        compose_step(compose_file, project, "image pull", "pull")
        compose_step(compose_file, project, "container start", "up", "-d")
        for service in ("database", "server", "web"):
            wait_healthy(
                compose_file,
                project,
                service,
                config["runtime"]["startup_timeout"],
            )
        wait_api(config)
        bootstrap_api_identity(config, bao, token)
    finally:
        bao.revoke_self(token)
    print(f"[OK] Zabbix {config['zabbix']['version']} API and Atlas token are ready")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"[ERROR] {exc}")
