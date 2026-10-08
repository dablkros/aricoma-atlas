import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from atlas import proxy
from atlas.openbao_client import OpenBaoError, OpenBaoNotFound


def customer_config():
    return {
        "proxy": {"docker_image": "nginx:1.30.5", "listen_address": "192.0.2.10",
                  "https_port": 443, "allowed_networks": ["192.0.2.0/24"]},
        "tls": {"ca_certificate": "/etc/atlas/ca.pem"},
        "services": {
            name: {"hostname": f"{name}.example.test", "certificate": f"/etc/atlas/{name}.crt",
                   "private_key": f"/etc/atlas/{name}.key"}
            for name in ("netbox", "oxidized")
        },
    }


class FakeBao:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.writes = value, error, 0

    def kv_read(self, token, path):
        if self.error:
            raise self.error
        if self.value is None:
            raise OpenBaoNotFound("not found")
        return copy.deepcopy(self.value)

    def kv_write(self, token, path, value):
        self.value = copy.deepcopy(value)
        self.writes += 1


class IngressTests(unittest.TestCase):
    def load(self, config):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy.yaml"
            path.write_text(yaml.safe_dump(config))
            return proxy.load_config(path, validate_tls=False)

    def test_customer_config_requires_explicit_scope(self):
        self.load(customer_config())
        for field, value in (("allowed_networks", []), ("allowed_networks", ["0.0.0.0/0"]),
                             ("listen_address", "0.0.0.0"), ("https_port", True)):
            with self.subTest(field=field, value=value):
                config = customer_config()
                config["proxy"][field] = value
                with self.assertRaises(ValueError):
                    self.load(config)

    def test_nginx_hostname_injection_rejected(self):
        for name in ("oxidized.example.test; auth_basic off; #", "*.example.test", ""):
            config = customer_config()
            config["services"]["oxidized"]["hostname"] = name
            with self.assertRaises(ValueError):
                self.load(config)

    def test_credentials_are_reused_and_access_errors_are_fatal(self):
        bao = FakeBao()
        first = proxy.ensure_web_secret(bao, "token")
        self.assertEqual(first, proxy.ensure_web_secret(bao, "token"))
        self.assertEqual(bao.writes, 1)
        self.assertGreaterEqual(len(first["password"]), 48)
        denied = FakeBao(error=OpenBaoError("HTTP 403"))
        with self.assertRaises(OpenBaoError):
            proxy.ensure_web_secret(denied, "token")
        self.assertEqual(denied.writes, 0)

    def test_malformed_existing_secret_is_not_replaced(self):
        bao = FakeBao({"username": "admin", "password": "short"})
        with self.assertRaises(ValueError):
            proxy.ensure_web_secret(bao, "token")
        self.assertEqual(bao.writes, 0)

    def test_oxidized_authentication_inherits_over_all_routes(self):
        text = proxy.render_nginx(customer_config())
        oxidized = text.split("server_name oxidized.example.test;", 1)[1]
        self.assertLess(oxidized.index("auth_basic "), oxidized.index("location /"))
        self.assertNotIn("auth_basic off", text)
        self.assertIn("deny all;", oxidized)
        self.assertIn("satisfy all;", oxidized)
        self.assertIn("ssl_reject_handshake on;", text)
        self.assertIn('proxy_set_header Authorization "";', oxidized)

    def test_zabbix_ui_uses_the_existing_tls_proxy_model(self):
        config = customer_config()
        config["services"]["zabbix"] = {
            "hostname": "zabbix.example.test",
            "certificate": "/etc/atlas/zabbix.crt",
            "private_key": "/etc/atlas/zabbix.key",
        }

        text = proxy.render_nginx(config)

        zabbix = text.split("server_name zabbix.example.test;", 1)[1]
        self.assertIn("http://atlas-zabbix-web:8080", zabbix)
        self.assertNotIn("auth_basic", zabbix.split("    }", 1)[0])
        self.assertIn("deny all;", zabbix)

    def test_generated_proxy_has_hash_only_and_restricted_private_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = customer_config()
            for service in config["services"].values():
                for field in ("certificate", "private_key"):
                    path = root / Path(service[field]).name
                    path.write_text("fixture")
                    service[field] = str(path)
            password = "private-test-password-only-123456789"
            with patch.object(proxy, "ROOT", root):
                compose_file = proxy.prepare_runtime(config, {"username": "admin", "password": password})
            for file in compose_file.parent.iterdir():
                self.assertNotIn(password, file.read_text())
            self.assertIn("admin:$6$", (compose_file.parent / "oxidized.htpasswd").read_text())
            self.assertEqual((compose_file.parent / "oxidized.key").stat().st_mode & 0o777, 0o600)
            compose = yaml.safe_load(compose_file.read_text())
            self.assertEqual(compose["services"]["proxy"]["ports"], ["192.0.2.10:443:8443"])

    def test_oxidized_has_no_published_port_and_preserves_source(self):
        spec = importlib.util.spec_from_file_location("deploy_oxidized", proxy.ROOT / "scripts/deploy_oxidized.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, "ROOT", Path(directory)), \
             patch.dict("os.environ", {"ATLAS_OXIDIZED_RUN_DIR": ""}):
            config = {"runtime": {"directory": ".runtime/oxidized"},
                      "oxidized": {"docker_image": "oxidized/oxidized:0.37.0", "interval": 3600,
                                   "threads": 10, "timeout": 20}}
            file = module.prepare_runtime(config, root=Path(directory))
            source = module.inventory_path(config, root=Path(directory))

            self.assertFalse(module.has_inventory(source))

            module.prepare_runtime(config, root=Path(directory))

            self.assertEqual(json.loads(source.read_text()), [])
            self.assertFalse(module.has_inventory(source))

            source.write_text(
                '[{"name":"fixture","ip":"127.0.0.2","model":"ios",'
                '"username":"ci","password":"ci-only"}]\n'
            )
            self.assertTrue(module.has_inventory(source))
            compose = yaml.safe_load(file.read_text())
            self.assertNotIn("ports", compose["services"]["oxidized"])
            self.assertEqual(compose["volumes"]["atlas_oxidized_data"]["name"], "atlas-oxidized-data")

    def test_real_ca_validation_rejects_wrong_name_key_and_ca(self):
        spec = importlib.util.spec_from_file_location("ci_ingress", proxy.ROOT / "scripts/ci_ingress.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deployment").mkdir()
            (root / "deployment/proxy.example.yaml").write_text(
                (proxy.ROOT / "deployment/proxy.example.yaml").read_text())
            with patch.object(module, "ROOT", root):
                module.fixtures(seed_inventory=False)
            path = root / ".runtime/proxy.yaml"
            config = proxy.load_config(path)
            service = config["services"]["oxidized"]
            wrong_name = dict(service, hostname="untrusted.atlas.test")
            with self.assertRaises(RuntimeError):
                proxy.validate_certificate("oxidized", wrong_name, config["tls"]["ca_certificate"])
            wrong_key = dict(service, private_key=str(root / ".runtime/ci-tls/ca.key"))
            with self.assertRaises(ValueError):
                proxy.validate_certificate("oxidized", wrong_key, config["tls"]["ca_certificate"])
            with self.assertRaises(RuntimeError):
                proxy.validate_certificate("oxidized", service, service["certificate"])


if __name__ == "__main__":
    unittest.main()
