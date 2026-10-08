#!/usr/bin/env python3
"""CI-only certificates and real HTTPS/OpenBao/persistence checks (no production secrets)."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.deployment import compose, connect_openbao, run, write_private, write_yaml  # noqa: E402
from atlas.proxy import SECRET_PATH, load_config  # noqa: E402


def fixtures(seed_inventory=True):
    runtime = ROOT / ".runtime/ci-tls"
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT / ".runtime", 0o700)
    os.chmod(runtime, 0o700)
    ca, ca_key = runtime / "ca.crt", runtime / "ca.key"
    certificate, key = runtime / "server.crt", runtime / "server.key"
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=Atlas ephemeral CI CA", "-keyout", str(ca_key), "-out", str(ca),
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-addext", "subjectKeyIdentifier=hash"])
    csr = runtime / "server.csr"
    run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=netbox.atlas.test",
         "-keyout", str(key), "-out", str(csr)])
    extension = runtime / "extensions.cnf"
    extension.write_text(
        "subjectAltName=DNS:netbox.atlas.test,DNS:oxidized.atlas.test,"
        "DNS:zabbix.atlas.test\n"
        "extendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
    )
    run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key),
         "-CAcreateserial", "-days", "2", "-out", str(certificate), "-extfile", str(extension)])
    for private in (key, ca_key):
        os.chmod(private, 0o600)
    config = yaml.safe_load((ROOT / "deployment/proxy.example.yaml").read_text())
    config["proxy"].update({"listen_address": "127.0.0.1", "https_port": 8443,
                            # Docker SNAT for runner-to-container traffic; CI only.
                            "allowed_networks": ["127.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]})
    config["tls"]["ca_certificate"] = str(ca)
    for name, service in config["services"].items():
        service.update({"hostname": f"{name}.atlas.test", "certificate": str(certificate),
                        "private_key": str(key)})
    write_yaml(ROOT / ".runtime/proxy.yaml", config)
    if seed_inventory:
        from scripts.deploy_oxidized import inventory_path, prepare_runtime
        oxidized = yaml.safe_load((ROOT / "deployment/oxidized.yaml").read_text())
        oxidized["oxidized"]["interval"] = 0
        # CI-only checkout: keep interval=0 through subsequent runtime generation.
        write_yaml(ROOT / "deployment/oxidized.yaml", oxidized)
        file = prepare_runtime(oxidized, root=ROOT)
        settings_file = file.parent / "config"
        settings = yaml.safe_load(settings_file.read_text())
        settings["interval"] = 0
        write_yaml(settings_file, settings)
        os.chmod(settings_file, 0o644)
        # Real Oxidized refuses an empty source; the CI fixture is loopback only
        # and interval=0 prevents SSH jobs. Values are isolated test data.
        inventory = [{
            "name": "CI-OXIDIZED-FIXTURE",
            "ip": "127.0.0.2",
            "model": "ios",
            "username": "ci-fixture",
            "password": "ci-fixture-only",
        }]
        write_private(
            inventory_path(oxidized, root=ROOT),
            json.dumps(inventory, indent=2) + "\n",
            mode=0o644,
        )
    print("[OK] Ephemeral CI CA and ingress configuration prepared")


def verify(snapshot=False, compare=False, oxidized_only=False):
    config = load_config()
    client, token = connect_openbao()
    try:
        secret = client.kv_read(token, SECRET_PATH)
    finally:
        client.revoke_self(token)
    session = requests.Session()
    session.trust_env = False
    session.verify = config["tls"]["ca_certificate"]
    port = config["proxy"]["https_port"]
    oxidized = f"https://{config['services']['oxidized']['hostname']}:{port}"
    for path in ("/", "/nodes.json", "/node/fetch/nonexistent", "/reload.json"):
        response = session.get(oxidized + path, timeout=15, allow_redirects=False)
        if response.status_code != 401:
            raise RuntimeError(f"Unauthenticated {path} was not rejected: {response.status_code}")
    response = session.get(oxidized + "/nodes.json", auth=(secret["username"], "wrong-password"), timeout=15)
    if response.status_code != 401:
        raise RuntimeError("Invalid password was not rejected")
    response = session.get(oxidized + "/nodes.json", auth=(secret["username"], secret["password"]), timeout=15)
    if response.status_code != 200 or not isinstance(response.json(), list):
        raise RuntimeError("Authenticated Oxidized API is unavailable")
    if not oxidized_only:
        netbox = f"https://{config['services']['netbox']['hostname']}:{port}"
        response = session.get(netbox + "/login/", timeout=15)
        if response.status_code != 200 or "csrftoken" not in session.cookies:
            raise RuntimeError("NetBox HTTPS login is unavailable")
        # A real login POST catches origin/proxy/secure-cookie regressions.
        client, token = connect_openbao()
        try:
            admin = client.kv_read(token, "netbox/admin")
        finally:
            client.revoke_self(token)
        response = session.post(netbox + "/login/", data={
            "username": admin["username"], "password": admin["password"],
            "csrfmiddlewaretoken": session.cookies["csrftoken"],
        }, headers={"Referer": netbox + "/login/", "Origin": netbox}, timeout=15, allow_redirects=False)
        if response.status_code != 302 or "sessionid" not in session.cookies:
            raise RuntimeError("NetBox HTTPS login POST failed")
    file = ROOT / ".runtime/oxidized/docker-compose.yml"
    container = compose(file, "atlas-oxidized", "ps", "-q", "oxidized")
    inspected = json.loads(run(["docker", "inspect", container]))[0]
    if any(inspected["NetworkSettings"]["Ports"].values()):
        raise RuntimeError("Oxidized has published host ports; authentication can be bypassed")
    repo = "/home/oxidized/.config/oxidized/repository.git"
    def git(*args):
        return run(["docker", "exec", "-i", container, "gosu", "oxidized", "git", f"--git-dir={repo}", *args], input="")
    if snapshot:
        # Seed an actual Git commit even though the initial device source is empty.
        run(["docker", "exec", container, "gosu", "oxidized", "git", "init", "--bare", repo])
        tree = git("mktree")
        commit = run(["docker", "exec", "-e", "GIT_AUTHOR_NAME=Atlas CI", "-e", "GIT_AUTHOR_EMAIL=ci@localhost",
                      "-e", "GIT_COMMITTER_NAME=Atlas CI", "-e", "GIT_COMMITTER_EMAIL=ci@localhost",
                      container, "gosu", "oxidized", "git", f"--git-dir={repo}", "commit-tree", tree, "-m", "CI persistence marker"])
        git("update-ref", "refs/heads/atlas-ci", commit)
        write_private(ROOT / ".runtime/ci-ingress-snapshot.json", json.dumps({"secret": secret, "commit": commit}))
    if compare:
        previous = json.loads((ROOT / ".runtime/ci-ingress-snapshot.json").read_text())
        if previous["secret"] != secret or git("rev-parse", "refs/heads/atlas-ci") != previous["commit"]:
            raise RuntimeError("Oxidized credentials or Git history changed during redeployment")
    print("[OK] Trusted HTTPS, Oxidized authentication and port isolation verified")
    if compare:
        print("[OK] Credentials and Oxidized Git history survived redeployment")


def verify_waiting():
    config = load_config()
    client, token = connect_openbao()
    try:
        secret = client.kv_read(token, SECRET_PATH)
    finally:
        client.revoke_self(token)

    session = requests.Session()
    session.trust_env = False
    session.verify = config["tls"]["ca_certificate"]

    base_url = (
        f"https://{config['services']['oxidized']['hostname']}:"
        f"{config['proxy']['https_port']}"
    )

    nodes_url = base_url + "/nodes.json"
    reload_url = base_url + "/reload"

    response = session.get(nodes_url, timeout=15)
    if response.status_code != 401:
        raise RuntimeError("Empty-inventory ingress is not authenticated")

    response = session.get(
        nodes_url,
        auth=(secret["username"], secret["password"]),
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Empty inventory must return HTTP 200 for nodes.json, got {response.status_code}"
        )

    if response.json() != []:
        raise RuntimeError("Empty inventory must return an empty node list")

    response = session.get(
        reload_url,
        auth=(secret["username"], secret["password"]),
        timeout=15,
    )
    if response.status_code != 503:
        raise RuntimeError(
            f"Reload must report waiting-for-inventory with HTTP 503, got {response.status_code}"
        )

    if response.json().get("status") != "waiting_for_inventory":
        raise RuntimeError("Reload did not report waiting_for_inventory")

    file = ROOT / ".runtime/oxidized/docker-compose.yml"
    container = compose(file, "atlas-oxidized", "ps", "--all", "-q", "oxidized")

    if not container:
        raise RuntimeError("Empty-inventory Oxidized container does not exist")

    state = json.loads(
        run(["docker", "inspect", container])
    )[0]["State"]

    if not state["Running"]:
        raise RuntimeError(
            "Empty-inventory Oxidized waiting container must remain running"
        )

    print("[OK] Empty inventory waiting state and HTTPS authentication verified")


def verify_network_denial():
    path = ROOT / ".runtime/proxy.yaml"
    config = load_config(path)
    original = path.read_text()
    client, token = connect_openbao()
    try:
        secret = client.kv_read(token, SECRET_PATH)
    finally:
        client.revoke_self(token)
    try:
        config["proxy"]["allowed_networks"] = ["192.0.2.0/24"]
        write_yaml(path, config)
        run([sys.executable, str(ROOT / "scripts/deploy_proxy.py")])
        session = requests.Session()
        session.trust_env = False
        session.verify = config["tls"]["ca_certificate"]
        url = f"https://{config['services']['oxidized']['hostname']}:{config['proxy']['https_port']}/nodes.json"
        for headers in ({}, {"X-Forwarded-For": "192.0.2.20"}):
            if session.get(url, auth=(secret["username"], secret["password"]), headers=headers, timeout=15).status_code != 403:
                raise RuntimeError("Management CIDR restriction can be bypassed")
    finally:
        write_private(path, original)
        run([sys.executable, str(ROOT / "scripts/deploy_proxy.py")])
    print("[OK] Unauthorized client network is denied, including spoofed X-Forwarded-For")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["fixtures", "verify", "snapshot", "compare", "waiting", "deny-network"])
    parser.add_argument("--oxidized-only", action="store_true")
    parser.add_argument("--empty-inventory", action="store_true")
    args = parser.parse_args()
    if args.operation == "fixtures":
        fixtures(seed_inventory=not args.empty_inventory)
    elif args.operation == "waiting":
        verify_waiting()
    elif args.operation == "deny-network":
        verify_network_denial()
    else:
        verify(snapshot=args.operation == "snapshot", compare=args.operation == "compare", oxidized_only=args.oxidized_only)


if __name__ == "__main__":
    main()
