import importlib.util
import grp
import os
import pwd
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_module():
    path = ROOT / "scripts/configure_site.py"
    spec = importlib.util.spec_from_file_location("configure_site_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SiteConfiguratorTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.ca = self.directory / "ca.crt"
        self.certificate = self.directory / "server.crt"
        self.private_key = self.directory / "server.key"
        for path in (self.ca, self.certificate, self.private_key):
            path.write_text("fixture", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def site(self):
        services = {}
        for name in self.module.SERVICES:
            services[name] = {
                "hostname": f"{name}.atlas.example.internal",
                "certificate": str(self.certificate),
                "private_key": str(self.private_key),
            }
        return {
            "version": 1,
            "proxy": {
                "listen_address": "10.20.30.40",
                "https_port": 443,
                "allowed_networks": ["10.20.30.0/24"],
            },
            "tls": {
                "mode": "existing",
                "ca_certificate": str(self.ca),
            },
            "services": services,
            "monitoring": {"source_ip": "10.20.30.40"},
        }

    def test_single_site_input_renders_proxy_and_monitoring_contracts(self):
        site = self.module.normalize_site(self.site(), self.directory)

        proxy = self.module.proxy_document(site)
        monitoring = self.module.monitoring_document(site, installed=True)

        self.assertEqual(proxy["proxy"]["listen_address"], "10.20.30.40")
        self.assertEqual(
            set(proxy["services"]), {"netbox", "oxidized", "zabbix"}
        )
        self.assertEqual(
            monitoring["allowed_device_networks"],
            ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        )
        self.assertEqual(monitoring["monitoring"]["source_ip"], "10.20.30.40")
        self.assertTrue(monitoring["fortios"]["tls_verify"])
        self.assertIsNone(site["inventory"]["default_tenant"])
        self.assertIsNone(site["inventory"]["default_site"])

    def test_site_input_normalizes_default_inventory_tenant(self):
        raw = self.site()
        raw["inventory"] = {
            "default_tenant": {
                "name": "ACME Slovakia",
                "slug": "acme-slovakia",
                "provision": True,
            },
            "default_site": {
                "name": "Bratislava DC",
                "slug": "bratislava-dc",
                "provision": True,
            },
        }

        site = self.module.normalize_site(raw, self.directory)

        self.assertEqual(site["inventory"]["default_tenant"]["slug"], "acme-slovakia")
        self.assertEqual(site["inventory"]["default_site"]["slug"], "bratislava-dc")
        self.assertEqual(site["inventory"]["management_interface"], "mgmt0")

    def test_site_input_supports_network_specific_egress_profiles(self):
        raw = self.site()
        raw["monitoring"] = {
            "allowed_device_networks": ["10.0.0.0/8", "172.16.0.0/12"],
            "egress_profiles": {
                "lab": {
                    "device_networks": ["10.255.253.0/24"],
                    "source_ip": "10.99.98.200",
                },
                "remote": {
                    "device_networks": ["172.20.0.0/16"],
                    "source_ip": "172.20.0.5",
                    "proxy_name": "remote-proxy",
                },
            },
        }

        site = self.module.normalize_site(raw, self.directory)
        with tempfile.TemporaryDirectory() as validation:
            monitoring_path = Path(validation) / "monitoring.yaml"
            monitoring_path.write_text(
                self.module.yaml.safe_dump(
                    self.module.monitoring_document(site), sort_keys=False
                ),
                encoding="utf-8",
            )
            self.module.load_monitoring_config(monitoring_path)

        monitoring = self.module.monitoring_document(site, installed=True)
        self.assertNotIn("source_ip", monitoring["monitoring"])
        self.assertEqual(
            monitoring["monitoring"]["egress_profiles"]["lab"]["source_ip"],
            "10.99.98.200",
        )
        self.assertEqual(
            monitoring["monitoring"]["egress_profiles"]["remote"]["proxy_name"],
            "remote-proxy",
        )

    def test_site_input_rejects_broad_or_ambiguous_networks(self):
        cases = (
            ("proxy", "allowed_networks", ["0.0.0.0/0"]),
            ("monitoring", "allowed_device_networks", ["0.0.0.0/0"]),
        )
        for section, field, value in cases:
            raw = self.site()
            raw[section][field] = value
            with self.subTest(section=section), self.assertRaisesRegex(
                ValueError, "entire address family"
            ):
                self.module.normalize_site(raw, self.directory)

    def test_lab_mode_is_explicit_and_bounded(self):
        raw = self.site()
        raw["tls"] = {"mode": "lab_generated", "validity_days": 30}
        for service in raw["services"].values():
            service.pop("certificate")
            service.pop("private_key")

        site = self.module.normalize_site(raw, self.directory)

        self.assertEqual(site["tls"], {"mode": "lab_generated", "validity_days": 30})
        raw["tls"]["validity_days"] = 365
        with self.assertRaisesRegex(ValueError, "between 1 and 90"):
            self.module.normalize_site(raw, self.directory)

    def test_lab_mode_generates_a_valid_short_lived_san_certificate(self):
        raw = self.site()
        raw["tls"] = {"mode": "lab_generated", "validity_days": 2}
        for service in raw["services"].values():
            service.pop("certificate")
            service.pop("private_key")
        site = self.module.normalize_site(raw, self.directory)
        staging = self.directory / "lab-staging"
        validation = self.directory / "validation"
        validation.mkdir()

        self.module.generate_lab_tls(site, staging)
        self.module.validate_documents(site, validation)

        self.assertTrue(Path(site["tls"]["ca_certificate"]).is_file())
        self.assertTrue(Path(site["services"]["zabbix"]["certificate"]).is_file())
        self.assertNotEqual(
            site["services"]["netbox"]["hostname"],
            site["services"]["zabbix"]["hostname"],
        )

    def test_existing_runtime_requires_explicit_force(self):
        site = self.module.normalize_site(self.site(), self.directory)
        target = self.directory / "target"
        existing = target / ".runtime/proxy.yaml"
        existing.parent.mkdir(parents=True)
        existing.write_text("existing", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "--force"):
            self.module._ensure_safe_targets(target, site, False)
        self.module._ensure_safe_targets(target, site, True)

    def test_lab_install_writes_protected_proxy_tls_and_monitoring_runtime(self):
        raw = self.site()
        raw["tls"] = {"mode": "lab_generated", "validity_days": 2}
        for service in raw["services"].values():
            service.pop("certificate")
            service.pop("private_key")
        site = self.module.normalize_site(raw, self.directory)
        target = self.directory / "appliance"
        owner = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name

        proxy, monitoring = self.module.install_site(
            site, target, owner, group
        )

        installed_proxy = self.module.yaml.safe_load(proxy.read_text(encoding="utf-8"))
        installed_monitoring = self.module.yaml.safe_load(
            monitoring.read_text(encoding="utf-8")
        )
        self.assertEqual(oct(proxy.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(monitoring.stat().st_mode & 0o777), "0o600")
        self.assertEqual(
            installed_proxy["services"]["zabbix"]["certificate"],
            str(target.resolve() / ".runtime/tls/zabbix.crt"),
        )
        self.assertEqual(
            installed_monitoring["monitoring"]["source_ip"], "10.20.30.40"
        )
        self.assertTrue((target / ".runtime/tls/ca.crt").is_file())
        self.assertFalse((target / ".runtime/tls/ca.key").exists())
        self.assertEqual(
            oct((target / ".runtime/inventory.yaml").stat().st_mode & 0o777),
            "0o600",
        )

    def test_fortios_ca_is_mapped_to_container_trust_path(self):
        raw = self.site()
        raw["fortios"] = {"ca_bundle": str(self.ca)}
        site = self.module.normalize_site(raw, self.directory)

        source = self.module.monitoring_document(site, installed=False)
        installed = self.module.monitoring_document(site, installed=True)

        self.assertEqual(source["fortios"]["ca_bundle"], str(self.ca))
        self.assertEqual(
            installed["fortios"]["ca_bundle"],
            "/run/atlas/monitoring/trust/fortigate-ca.pem",
        )

    def test_admin_cli_exposes_configure_command(self):
        source = (ROOT / "bin/atlasctl").read_text(encoding="utf-8")
        self.assertIn("sudo atlasctl configure", source)
        self.assertIn('configure)', source)
        self.assertIn("scripts/configure_site.py", source)
        self.assertIn("sudo atlasctl inventory", source)
        self.assertIn("scripts/import_netbox_inventory.py", source)


if __name__ == "__main__":
    unittest.main()
