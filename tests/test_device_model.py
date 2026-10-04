import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from atlas.device_credentials import (
    DeviceValidationError, credential_path, oxidized_reference,
    resolve_oxidized_device, validate_admin_secret, validate_snmp_secret,
)
from atlas.openbao_client import (
    OpenBaoAuthenticationError,
    OpenBaoClient,
    OpenBaoError,
    OpenBaoNotFound,
)
from atlas.platforms import (
    ROOT, default_platform, enrich_device_types, load_platform_config, provision_platforms,
)
from scripts import bootstrap_netbox, provision_baseline, provision_netbox


def device():
    return {"name": "SW-CORE-01", "platform": {"slug": "cisco-ios-xe"},
            "device_type": {"manufacturer": {"slug": "cisco"}},
            "primary_ip4": {"address": "10.10.10.1/32"},
            "custom_fields": {"oxidized_enabled": True, "credential_profile": "cisco-default"}}


class MemoryNetBox:
    """Remote-state fake: persist writes, record unexpected endpoint changes."""
    def __init__(self):
        self.tables = {endpoint: [] for endpoint in (
            "/api/dcim/platforms/", "/api/dcim/manufacturers/", "/api/dcim/device-types/",
            "/api/extras/custom-fields/",
            *(spec["endpoint"] for spec in provision_netbox.COMPONENT_SPECS.values()))}
        self.writes = []
        self.next_id = 1

    def get_all(self, endpoint):
        return copy.deepcopy(self.tables[endpoint])

    def post(self, endpoint, payload):
        obj = copy.deepcopy(payload)
        obj["id"] = self.next_id
        self.next_id += 1
        if endpoint == "/api/dcim/device-types/":
            obj["manufacturer"] = next(m for m in self.tables["/api/dcim/manufacturers/"]
                                       if m["id"] == obj["manufacturer"])
        self.tables[endpoint].append(obj)
        self.writes.append(("POST", endpoint, payload))
        return copy.deepcopy(obj)

    def bulk_post(self, endpoint, payloads, *args):
        return [self.post(endpoint, p) for p in payloads]

    def patch(self, endpoint, payload):
        prefix, pk, _ = endpoint.rsplit("/", 2)
        obj = next(o for o in self.tables[prefix + "/"] if o["id"] == int(pk))
        obj.update(copy.deepcopy(payload))
        self.writes.append(("PATCH", endpoint, payload))
        return copy.deepcopy(obj)


def item(slug="cisco-c9500-48y4c", model="Catalyst 9500-48Y4C"):
    return {"file": Path("fixture.yaml"), "manufacturer": "Cisco", "model": model, "data": {
        "manufacturer": "Cisco", "slug": slug, "model": model}}


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.config = load_platform_config()

    def test_actual_catalog_names_and_conservative_exclusions(self):
        for vendor, slug, model, expected in [
            ("Cisco", "cisco-c9200l-24t-4g-e", "C9200L-24T-4G-E", "cisco-ios-xe"),
            ("Cisco", "cisco-c9500-48y4c", "Catalyst 9500-48Y4C", "cisco-ios-xe"),
            ("Cisco", "cisco-catalyst-3850-48pt-12-mgig-plus-36-gig-upoe", "CATALYST 3850 48PT 12 MGIG+36 GIG UPOE", "cisco-ios-xe"),
            ("Cisco", "cisco-ws-c2960x-24ts-ll", "WS-C2960X-24TS-LL", "cisco-ios"),
            ("Fortinet", "fortinet-fg-60f", "FortiGate 60F", "fortios"),
            ("Fortinet", "fortinet-fap-231f", "FortiAP 231F", "fortiap"),
            ("Fortinet", "fortinet-faz-810g", "FortiAnalyzer 810G", "fortianalyzer"),
            ("Juniper", "juniper-ex2300-24t", "EX2300-24T", "junos"),
            ("Juniper", "juniper-qfx5120-48y", "QFX5120-48Y", "junos"),
            ("Juniper", "juniper-mx204", "MX204", "junos"),
            ("Juniper", "juniper-qfx5130-48c", "QFX5130-48C", None),
            ("Fortinet", "fortinet-fpm-7620f", "FortiGate Module 7620F", None),
            ("Cisco", "cisco-n9k-unknown", "N9K-unknown", None),
            ("Cisco", "unknown", "unknown", None),
        ]:
            with self.subTest(model=model):
                self.assertEqual(default_platform({"manufacturer": vendor, "slug": slug, "model": model}, self.config), expected)

    def test_conflicting_rules_fail(self):
        self.config["platform_mappings"]["Cisco"].append({"slugs": ["cisco-c9500-*"], "platform": "cisco-ios"})
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            default_platform(item()["data"], self.config)

    def test_mapping_is_tied_to_deployed_image(self):
        deployment = yaml.safe_load((ROOT / "deployment/oxidized.yaml").read_text())
        self.assertEqual(deployment["oxidized"]["docker_image"], self.config["oxidized"]["image"])
        self.assertEqual(self.config["oxidized_platform_mapping"]["fortios"]["model"], "fortigate")
        d = device()
        d["platform"]["slug"] = "cisco-nxos"
        self.assertEqual(oxidized_reference(d).model, "nxos")

    def test_complete_provisioning_twice_and_dry_run(self):
        client = MemoryNetBox()
        with redirect_stdout(io.StringIO()):
            provision_netbox.provision(client, [item()], False, 200, False)
            self.assertEqual(client.writes, [])
            provision_netbox.provision(client, [item()], True, 200, False)
            writes = copy.deepcopy(client.writes)
            provision_netbox.provision(client, [item()], True, 200, False)
            self.assertEqual(client.writes, writes)
        types = client.tables["/api/dcim/device-types/"]
        xe = next(p for p in client.tables["/api/dcim/platforms/"] if p["slug"] == "cisco-ios-xe")
        self.assertEqual(types[0]["default_platform"], xe["id"])
        self.assertFalse(any("/devices/" in entry[1] for entry in client.writes))

    def test_existing_type_enriched_then_unmapped_cleared_without_other_edits(self):
        client = MemoryNetBox()
        with redirect_stdout(io.StringIO()):
            manufacturers = provision_netbox.provision_manufacturers(client, [item()], True, False)
            c = copy.deepcopy(self.config)
            c["platforms"] = {k: v for k, v in c["platforms"].items() if v["manufacturer"] == "Cisco"}
            platforms = provision_platforms(client, manufacturers, c, True)
            obj = client.post("/api/dcim/device-types/", {"manufacturer": manufacturers["Cisco"]["id"],
                              "model": item()["model"], "description": "operator text", "default_platform": None})
            index = {("Cisco", obj["model"]): obj}
            self.assertEqual(enrich_device_types(client, [item()], index, platforms, c, False), 1)
            self.assertIsNone(client.tables["/api/dcim/device-types/"][0]["default_platform"])
            enrich_device_types(client, [item()], index, platforms, c, True)
            index = provision_netbox.load_device_type_index(client)
            self.assertEqual(enrich_device_types(client, [item()], index, platforms, c, True), 0)
            c["platform_mappings"]["Cisco"] = []
            enrich_device_types(client, [item()], index, platforms, c, True)
        stored = client.tables["/api/dcim/device-types/"][0]
        self.assertIsNone(stored["default_platform"])
        self.assertEqual(stored["description"], "operator text")
        self.assertTrue(all(set(w[2]) == {"default_platform"} for w in client.writes if w[0] == "PATCH"))

    def test_platform_manufacturer_conflict_is_fatal(self):
        client = MemoryNetBox()
        client.tables["/api/dcim/platforms/"] = [{"id": 1, "slug": "cisco-ios", "manufacturer": {"id": 99}}]
        with self.assertRaisesRegex(ValueError, "manufacturer conflict"), redirect_stdout(io.StringIO()):
            provision_platforms(client, {"Cisco": {"id": 2}}, self.config, True)

    def test_credential_custom_field_is_idempotent(self):
        client = MemoryNetBox()
        _, fields = provision_baseline.load_baseline()
        desired = [f for f in fields if f["name"] == "credential_profile"]
        self.assertEqual(len(desired), 1)
        with redirect_stdout(io.StringIO()):
            first = provision_baseline.provision_custom_fields(client, desired, {}, True)
            second = provision_baseline.provision_custom_fields(client, desired, {}, True)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["drift"], 0)

    def test_stale_mapping_invalidates_cached_build(self):
        manifest = bootstrap_netbox.load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "Cisco").mkdir()
            (build / "Cisco/a.yaml").write_text("{}")
            report = {"total": 1, "catalog": manifest["catalog"], "upstream_commit": manifest["upstream"]["commit"],
                      "vendors": {v: {"patterns": p} for v, p in bootstrap_netbox.expected_vendor_patterns(manifest).items()},
                      "platform_mapping_sha256": "outdated"}
            with patch.object(bootstrap_netbox, "DEVICE_TYPE_BUILD", build), \
                 patch.object(bootstrap_netbox, "load_catalog_report", return_value=report), \
                 patch.object(bootstrap_netbox, "get_upstream_origin", return_value=manifest["upstream"]["repository"]):
                current, reasons = bootstrap_netbox.catalog_status(manifest)
            self.assertFalse(current)
            self.assertIn("platform mapping changed or enrichment is missing", reasons)


class CredentialTests(unittest.TestCase):
    def test_reference_and_hostname_rename(self):
        d = device()
        ref = oxidized_reference(d)
        self.assertEqual(ref.path, "devices/credentials/cisco/cisco-default/admin")
        self.assertEqual(ref.model, "ios")
        self.assertEqual(ref.address, "10.10.10.1")
        d["name"] = "renamed"
        self.assertEqual(oxidized_reference(d).path, ref.path)

    def test_no_fallbacks(self):
        for field, value, message in [
            ("platform", None, "Missing NetBox platform"),
            ("platform", {"slug": "unknown"}, "No Oxidized mapping"),
            ("platform", {"slug": "cisco-cbs"}, "No Oxidized mapping"),
            ("custom_fields", {"oxidized_enabled": True}, "Missing credential_profile"),
            ("device_type", {}, "Missing manufacturer"),
            ("primary_ip4", None, "Missing or invalid primary IP"),
            ("name", None, "Missing device name"),
        ]:
            with self.subTest(field=field, value=value):
                d = device(); d[field] = value
                client = Mock()
                with self.assertRaisesRegex(DeviceValidationError, message):
                    resolve_oxidized_device(d, client, "fixture")
                client.kv_read.assert_not_called()

    def test_malformed_nested_fields_are_explicit_device_errors(self):
        cases = (
            ("platform", "cisco-ios-xe", "missing_platform"),
            ("device_type", "cisco", "missing_manufacturer"),
            ("primary_ip4", "10.10.10.1/32", "missing_primary_ip"),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                d = device()
                d[field] = value
                client = Mock()
                with self.assertRaises(DeviceValidationError) as raised:
                    resolve_oxidized_device(d, client, "fixture")
                self.assertEqual(raised.exception.code, code)
                client.kv_read.assert_not_called()

    def test_supported_platforms_and_ipv6_are_resolved_without_guessing(self):
        cases = (
            ("cisco-ios-xe", "cisco", "ios"),
            ("fortios", "fortinet", "fortigate"),
            ("junos", "juniper", "junos"),
        )
        for platform, vendor, model in cases:
            with self.subTest(platform=platform):
                d = device()
                d["platform"] = {"slug": platform}
                d["device_type"]["manufacturer"]["slug"] = vendor
                d["primary_ip4"] = None
                d["primary_ip6"] = {"address": "2001:db8::10/64"}
                client = Mock()
                client.kv_read.return_value = {
                    "username": "fixture-user",
                    "password": "fixture-password",
                }

                resolved = resolve_oxidized_device(d, client, "fixture-token")

                self.assertEqual(resolved.reference.model, model)
                self.assertEqual(resolved.reference.address, "2001:db8::10")

    def test_disabled_devices_do_not_read_secrets(self):
        for enabled in (False, None, "true", 1):
            d = device(); d["custom_fields"]["oxidized_enabled"] = enabled
            client = Mock()
            self.assertIsNone(resolve_oxidized_device(d, client, "fixture"))
            client.kv_read.assert_not_called()

    def test_paths_reject_traversal_and_preserve_profile_case(self):
        self.assertEqual(credential_path("cisco", "SW-CORE-01"), "devices/credentials/cisco/SW-CORE-01/admin")
        for bad in ("../other", "a/b", "a%2fb", "x?y", " x", "x\n", "", "..", "a\\b"):
            with self.subTest(value=bad), self.assertRaises(DeviceValidationError):
                credential_path("cisco", bad)
        with self.assertRaises(DeviceValidationError):
            credential_path("Cisco Systems, Inc.", "profile")
        with self.assertRaises(DeviceValidationError):
            credential_path("cisco", "profile", "oxidized")

    def test_secret_resolution_no_secret_repr_and_optional_enable(self):
        client = Mock()
        client.kv_read.return_value = {"username": "fixture-user", "password": "fixture-secret"}
        resolved = resolve_oxidized_device(device(), client, "fixture-token")
        self.assertNotIn("fixture-secret", repr(resolved))
        self.assertNotIn("fixture-user", repr(resolved))
        self.assertNotIn("enable_password", resolved.credentials)
        client.kv_read.assert_called_once_with("fixture-token", "devices/credentials/cisco/cisco-default/admin", mount="atlas")
        client.kv_read.return_value["enable_password"] = "fixture-enable"
        self.assertEqual(resolve_oxidized_device(device(), client, "fixture-token").credentials["enable_password"], "fixture-enable")

    def test_missing_and_denied_secrets_are_distinct_and_redacted(self):
        for error, message in [(OpenBaoNotFound("sensitive-body"), "Credential profile not found"),
                               (OpenBaoError("sensitive-body"), "Unable to read credential profile")]:
            with self.subTest(error=type(error)):
                client = Mock(); client.kv_read.side_effect = error
                with self.assertRaisesRegex(DeviceValidationError, message) as raised:
                    resolve_oxidized_device(device(), client, "fixture")
                self.assertNotIn("sensitive-body", str(raised.exception))
                self.assertTrue(raised.exception.__suppress_context__)

    def test_invalid_admin_schema_is_redacted(self):
        for secret in (None, {}, {"username": "fixture-user"}, {"username": "u", "password": ""},
                       {"username": "u", "password": "sensitive-body", "enable_password": 42}):
            with self.subTest(secret=secret), self.assertRaisesRegex(DeviceValidationError, "Invalid admin credential schema"):
                validate_admin_secret(secret)

    def test_snmp_contract(self):
        for secret in ({"version": "2c", "community": "fixture-community"},
                       {"version": "3", "username": "fixture", "auth_protocol": "SHA256", "auth_password": "fixture-auth",
                        "privacy_protocol": "AES", "privacy_password": "fixture-privacy"}):
            self.assertEqual(validate_snmp_secret(secret), secret)
        for secret in ({}, {"version": "2c"}, {"version": "3", "username": "fixture"}, {"version": []}):
            with self.assertRaises(DeviceValidationError):
                validate_snmp_secret(secret)

    def test_openbao_http_errors_do_not_echo_response_or_token(self):
        for status, expected in (
            (404, OpenBaoNotFound),
            (403, OpenBaoAuthenticationError),
            (500, OpenBaoError),
        ):
            response = Mock(status_code=status, text="sensitive-body")
            with patch("atlas.openbao_client.requests.request", return_value=response):
                with self.assertRaises(expected) as raised:
                    OpenBaoClient().kv_read("sensitive-token", "devices/credentials/cisco/p/admin")
            self.assertNotIn("sensitive", str(raised.exception))
            self.assertEqual(raised.exception.status_code, status)
