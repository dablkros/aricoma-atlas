"""Customer-specific HTTPS ingress configuration and generated Nginx runtime."""

import ipaddress
import os
import re
import secrets
import ssl
from pathlib import Path

import yaml

from atlas.deployment import ROOT, NETBOX_NETWORK, OXIDIZED_NETWORK, ZABBIX_NETWORK
from atlas.deployment import optional_secret, run, write_private, write_yaml

HOSTNAME = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SECRET_PATH = "oxidized/web"


def load_config(path=None, validate_tls=True):
    path = Path(path or os.environ.get("ATLAS_PROXY_CONFIG", ROOT / ".runtime/proxy.yaml"))
    if not path.is_file():
        raise RuntimeError("Copy deployment/proxy.example.yaml to .runtime/proxy.yaml and configure this VM first")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Proxy configuration must be an object")
    proxy = config["proxy"]
    address = ipaddress.ip_address(proxy["listen_address"])
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Proxy listen_address must be a specific management IP")
    proxy["listen_address"] = str(address)
    port = proxy["https_port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Proxy https_port must be an integer from 1 to 65535")
    networks = proxy["allowed_networks"]
    if not isinstance(networks, list) or not networks:
        raise ValueError("At least one management subnet is required")
    proxy["allowed_networks"] = [str(ipaddress.ip_network(value)) for value in networks]
    if any(ipaddress.ip_network(value).prefixlen == 0 for value in networks):
        raise ValueError("Do not allow the entire Internet; specify management subnets")
    image = proxy["docker_image"]
    if not isinstance(image, str) or not re.fullmatch(r"[a-zA-Z0-9./_-]+:[0-9][a-zA-Z0-9._-]*", image):
        raise ValueError("Proxy docker_image must use an explicit version tag")
    service_names = set(config["services"])
    if service_names not in (
        {"netbox", "oxidized"},
        {"netbox", "oxidized", "zabbix"},
    ):
        raise ValueError("Configure netbox and oxidized, with optional zabbix ingress")
    hostnames = []
    for name, service in config["services"].items():
        hostname = service["hostname"]
        if not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname):
            raise ValueError(f"Invalid DNS hostname for {name}")
        hostnames.append(hostname)
        for field in ("certificate", "private_key"):
            filename = Path(service[field])
            if not service[field] or not filename.is_absolute():
                raise ValueError(f"{name}.{field} must be an absolute PEM file path")
            if validate_tls and not filename.is_file():
                raise ValueError(f"Missing TLS file for {name}: {field}")
    if len(set(hostnames)) != len(hostnames):
        raise ValueError("Every proxied service must use a unique DNS hostname")
    ca = config["tls"]["ca_certificate"]
    if not ca or not Path(ca).is_absolute():
        raise ValueError("tls.ca_certificate must be an absolute PEM CA bundle path")
    if validate_tls:
        for name, service in config["services"].items():
            validate_certificate(name, service, ca)
    return config


def validate_certificate(name, service, ca):
    certificate, key = service["certificate"], service["private_key"]
    # load_cert_chain checks PEM format, unencrypted private key and key match.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certificate, key, password=lambda: "")
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(f"Invalid certificate/key pair for {name}") from exc
    run(["openssl", "x509", "-in", certificate, "-noout", "-checkend", "0"])
    run(["openssl", "verify", "-x509_strict", "-CAfile", ca, "-untrusted", certificate,
         "-purpose", "sslserver", "-verify_hostname", service["hostname"], certificate])


def ensure_web_secret(client, token):
    secret = optional_secret(client, token, SECRET_PATH)
    if secret is None:
        secret = {"username": "oxidized-admin", "password": secrets.token_urlsafe(48)}
        client.kv_write(token, SECRET_PATH, secret)
    # Never silently replace a malformed existing credential.
    if not isinstance(secret, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(secret.get("username", ""))):
        raise ValueError("Invalid username in atlas/oxidized/web")
    password = secret.get("password")
    if not isinstance(password, str) or len(password) < 20 or "\n" in password or "\r" in password or "\x00" in password:
        raise ValueError("Invalid password in atlas/oxidized/web (minimum 20 characters)")
    return secret


def password_hash(password):
    # SHA-512 crypt is supported by Nginx's crypt() on the Debian image.
    # Password travels only through stdin, never argv/environment/log output.
    return run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n")


def render_nginx(config):
    proxy = config["proxy"]
    allow = "\n".join(f"        allow {network};" for network in proxy["allowed_networks"])
    servers = []
    for name, service in config["services"].items():
        upstream = {
            "netbox": "atlas-netbox:8080",
            "oxidized": "atlas-oxidized:8888",
            "zabbix": "atlas-zabbix-web:8080",
        }[name]
        authentication = ""
        if name == "oxidized":
            authentication = ('        auth_basic "Atlas Oxidized";\n'
                              '        auth_basic_user_file /etc/atlas/oxidized.htpasswd;\n')
        authorization = '            proxy_set_header Authorization "";\n' if name == "oxidized" else ""
        unavailable = ""
        if name == "oxidized":
            unavailable = ('        error_page 502 504 =503 @oxidized_unavailable;\n'
                           '        location @oxidized_unavailable { return 503 "Oxidized backend unavailable.\\n"; }\n')
        servers.append(f"""
    server {{
        listen 8443 ssl;
        server_name {service['hostname']};
        ssl_certificate /etc/atlas/{name}.crt;
        ssl_certificate_key /etc/atlas/{name}.key;
{allow}
        deny all;
        satisfy all;
{authentication}
{unavailable}
        location / {{
            set $backend http://{upstream};
            proxy_pass $backend;
{authorization}            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Host $http_host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-Port {proxy['https_port']};
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_read_timeout 120s;
        }}
    }}
""")
    return """worker_processes auto;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    access_log /dev/stdout;
    server_tokens off;
    resolver 127.0.0.11 valid=10s ipv6=off;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 25m;
    server {
        listen 8443 ssl default_server;
        ssl_reject_handshake on;
    }
""" + "".join(servers) + "}\n"


def prepare_runtime(config, secret):
    runtime = ROOT / ".runtime/proxy"
    runtime.mkdir(parents=True, exist_ok=True)
    # Workers must be able to read the hash file inside the mounted directory.
    # The parent .runtime directory remains owner-only on the host.
    os.chmod(ROOT / ".runtime", 0o700)
    os.chmod(runtime, 0o755)
    write_private(runtime / "nginx.conf", render_nginx(config), 0o644)
    digest = password_hash(secret["password"])
    write_private(runtime / "oxidized.htpasswd", f"{secret['username']}:{digest}\n", 0o644)
    for name, service in config["services"].items():
        write_private(runtime / f"{name}.crt", Path(service["certificate"]).read_text(), 0o644)
        write_private(runtime / f"{name}.key", Path(service["private_key"]).read_text())
    address = config["proxy"]["listen_address"]
    if ":" in address:
        address = f"[{address}]"
    file = runtime / "docker-compose.yml"
    service_networks = ["netbox", "oxidized"]
    if "zabbix" in config["services"]:
        service_networks.append("zabbix")
    networks = {
        "netbox": {"external": True, "name": NETBOX_NETWORK},
        "oxidized": {"external": True, "name": OXIDIZED_NETWORK},
    }
    if "zabbix" in config["services"]:
        networks["zabbix"] = {"external": True, "name": ZABBIX_NETWORK}
    write_yaml(file, {
        "services": {"proxy": {
            "image": config["proxy"]["docker_image"], "restart": "unless-stopped",
            "command": ["nginx", "-c", "/etc/atlas/nginx.conf", "-g", "daemon off;"],
            "ports": [f"{address}:{config['proxy']['https_port']}:8443"],
            "volumes": [f"{runtime}:/etc/atlas:ro"],
            "networks": service_networks,
            "healthcheck": {
                "test": ["CMD", "nginx", "-c", "/etc/atlas/nginx.conf", "-t"],
                "interval": "10s", "timeout": "5s", "retries": 6,
            },
        }},
        "networks": networks,
    })
    return file
