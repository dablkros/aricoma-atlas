import unittest

from atlas.api.schemas.credentials import CredentialOnboardingRequest
from atlas.api.routes.credentials import _plan_hash
from atlas.services.credential_onboarding import (
    CredentialOnboardingInvalid,
    CredentialOnboardingService,
)
from atlas.services.errors import DependencyError


class FakeSecrets:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def read_secret_with_metadata(self, path):
        if path not in self.values:
            raise DependencyError("secret_not_found")
        value, version = self.values[path]
        return dict(value), {"version": version}

    def write_secret(self, path, data, *, cas):
        current = self.values.get(path)
        expected = 0 if current is None else current[1]
        if cas != expected:
            raise DependencyError("unavailable")
        version = 1 if current is None else current[1] + 1
        self.values[path] = (dict(data), version)
        self.writes.append((path, dict(data), cas))


class FakeNetBox:
    def __init__(self):
        self.device = {
            "id": 7,
            "name": "forti-mt-2",
            "status": {"value": "active"},
            "device_type": {
                "manufacturer": {"name": "Fortinet", "slug": "fortinet"},
            },
            "platform": None,
            "custom_fields": {"site_note": "preserve"},
        }
        self.devices = {7: self.device}
        self.platforms = [{"id": 12, "slug": "fortios", "name": "FortiOS"}]
        self.services = []
        self.posts = []
        self.patches = []

    def get(self, endpoint):
        device_id = self.device_id_from_endpoint(endpoint)
        return self.devices[device_id]

    @staticmethod
    def device_id_from_endpoint(endpoint):
        prefix = "/api/dcim/devices/"
        if not endpoint.startswith(prefix) or not endpoint.endswith("/"):
            raise AssertionError(endpoint)
        return int(endpoint[len(prefix):-1])

    def get_all(self, endpoint, params=None):
        if endpoint == "/api/dcim/platforms/":
            return [item for item in self.platforms if item["slug"] == params["slug"]]
        if endpoint == "/api/dcim/devices/":
            return list(self.devices.values())
        raise AssertionError((endpoint, params))

    def get_device_services(self, device_id, name):
        if device_id not in self.devices or name != "fortios-api":
            raise AssertionError((device_id, name))
        return list(self.services if device_id == 7 else [])

    def post(self, endpoint, payload):
        if endpoint != "/api/ipam/services/":
            raise AssertionError(endpoint)
        service = {"id": 50, **payload}
        if payload["device"] == 7:
            self.services.append(service)
        self.posts.append((endpoint, payload))
        return service

    def patch(self, endpoint, payload):
        device = self.devices[self.device_id_from_endpoint(endpoint)]
        device["platform"] = {"id": payload["platform"], "slug": "fortios"}
        device["custom_fields"] = dict(payload["custom_fields"])
        self.patches.append((endpoint, payload))
        return self.device


def api_request(**overrides):
    values = {
        "device_ids": [7],
        "platform": "fortios",
        "credential_profile": "forti-mt",
        "prophylaxis_enabled": True,
        "prophylaxis_auth": "api_token",
        "prophylaxis_api": {"api_token": "fixture-api-token"},
        "fortios_api_port": 444,
    }
    values.update(overrides)
    return CredentialOnboardingRequest(**values)


class CredentialOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.netbox = FakeNetBox()
        self.secrets = FakeSecrets()
        self.service = CredentialOnboardingService(
            self.netbox, self.secrets, self.secrets
        )

    def test_plan_is_secret_free_and_creates_exact_purpose_leaf(self):
        result = self.service.plan(api_request())

        self.assertTrue(result["apply_allowed"])
        self.assertIn(
            {
                "target": "openbao",
                "purpose": "prophylaxis",
                "path": "devices/credentials/fortinet/forti-mt/prophylaxis",
                "action": "CREATE",
                "version": 0,
                "blocked": False,
            },
            result["operations"],
        )
        self.assertNotIn("fixture-api-token", repr(result))

    def test_plan_fingerprint_is_keyed_and_changes_with_secret_value(self):
        first = api_request()
        second = api_request(prophylaxis_api={"api_token": "other-token"})

        self.assertNotEqual(
            _plan_hash(first, {"operations": []}, "operator-token-a"),
            _plan_hash(second, {"operations": []}, "operator-token-a"),
        )
        self.assertNotEqual(
            _plan_hash(first, {"operations": []}, "operator-token-a"),
            _plan_hash(first, {"operations": []}, "operator-token-b"),
        )
        self.assertNotIn("fixture-api-token", repr(first))

    def test_existing_different_secret_requires_explicit_replace(self):
        path = "devices/credentials/fortinet/forti-mt/prophylaxis"
        self.secrets.values[path] = ({"api_token": "old-token"}, 6)

        blocked = self.service.plan(api_request())
        replacement = self.service.plan(api_request(replace_existing=True))

        self.assertFalse(blocked["apply_allowed"])
        self.assertEqual(blocked["operations"][0]["action"], "CONFLICT")
        self.assertTrue(replacement["apply_allowed"])
        self.assertEqual(replacement["operations"][0]["action"], "UPDATE")

    def test_ssh_is_blocked_when_fortios_api_service_selects_api(self):
        self.netbox.services = [{"id": 50, "name": "fortios-api", "protocol": {"value": "tcp"}, "ports": [444]}]
        request = api_request(
            prophylaxis_auth="ssh",
            prophylaxis_api=None,
            prophylaxis_ssh={"username": "reader", "password": "password"},
            fortios_api_port=None,
        )

        result = self.service.plan(request)

        self.assertFalse(result["apply_allowed"])
        self.assertEqual(result["operations"][1]["reason"], "fortios_api_service_selects_api_transport")

    def test_apply_uses_cas_creates_service_and_preserves_custom_fields(self):
        request = api_request()
        result = self.service.apply(
            request,
            expected_plan=self.service.plan(request),
        )

        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(self.secrets.writes[0][2], 0)
        self.assertEqual(self.netbox.posts[0][1]["ports"], [444])
        fields = self.netbox.patches[0][1]["custom_fields"]
        self.assertEqual(fields["site_note"], "preserve")
        self.assertEqual(fields["credential_profile"], "forti-mt")
        self.assertTrue(fields["profylaxia_enabled"])
        self.assertFalse(fields["oxidized_enabled"])

    def test_platform_manufacturer_mismatch_is_rejected(self):
        self.netbox.device["device_type"]["manufacturer"] = {
            "name": "Cisco", "slug": "cisco"
        }

        with self.assertRaisesRegex(
            CredentialOnboardingInvalid, "does not belong"
        ):
            self.service.plan(api_request())

    def test_batch_requires_one_manufacturer_and_plans_each_device(self):
        second = {
            **self.netbox.device,
            "id": 8,
            "name": "forti-mt-3",
            "custom_fields": {},
        }
        self.netbox.devices[8] = second

        result = self.service.plan(api_request(device_ids=[7, 8]))

        self.assertTrue(result["apply_allowed"])
        self.assertEqual([7, 8], [item["id"] for item in result["devices"]])
        device_operations = [
            item for item in result["operations"] if item.get("object") == "device"
        ]
        self.assertEqual([7, 8], [item["device"]["id"] for item in device_operations])

        second["device_type"] = {
            "manufacturer": {"name": "Cisco", "slug": "cisco"},
        }
        with self.assertRaisesRegex(CredentialOnboardingInvalid, "same manufacturer"):
            self.service.plan(api_request(device_ids=[7, 8]))


if __name__ == "__main__":
    unittest.main()
