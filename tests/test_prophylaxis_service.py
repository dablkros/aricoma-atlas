import unittest

from atlas.device_credentials import DeviceValidationError, credential_path
from atlas.prophylaxis.errors import AuthenticationFailedError
from atlas.prophylaxis.models import CheckId, RawCheckResult
from atlas.prophylaxis.service import (
    ProphylaxisService,
    device_target,
    selected_check_values,
)


def netbox_device(platform="cisco-ios-xe", checks=None):
    return {
        "id": 10,
        "name": "SW-CORE-01",
        "status": {"value": "active", "label": "Active"},
        "primary_ip4": {"address": "10.10.10.1/32"},
        "primary_ip6": None,
        "platform": {"slug": platform},
        "device_type": {
            "manufacturer": {
                "slug": "fortinet" if platform == "fortios" else "cisco"
            }
        },
        "custom_fields": {
            "credential_profile": "default",
            "profylaxia_enabled": True,
            "profylaxia_checks": checks
            or [{"value": "cpu_utilization", "label": "CPU utilization"}],
        },
    }


class FakeNetBox:
    def __init__(self, devices, services=None):
        self.devices = devices
        self.services = services or []
        self.calls = []

    def get_all(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.devices

    def get_prophylaxis_devices(self):
        return self.devices

    def get_device_services(self, device_id, name):
        self.calls.append(
            ("/api/ipam/services/", {"device_id": device_id, "name": name})
        )
        return self.services


class FakeOpenBao:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get_device_credentials(self, vendor, profile, *, authentication="ssh"):
        self.calls.append((vendor, profile, authentication))
        if self.error:
            raise self.error
        if authentication == "api_token":
            return {"api_token": "fixture-token"}
        return {"username": "fixture-user", "password": "fixture-password"}


class FakeBackend:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        profile = kwargs["profile"]
        if profile.slug == "fortios":
            return RawCheckResult(
                check=CheckId.CPU_UTILIZATION,
                platform="fortios",
                status="ok",
                data={"cpu": [{"current": 12}]},
            )
        return RawCheckResult(
            check=CheckId.CPU_UTILIZATION,
            platform=profile.slug,
            status="ok",
            raw_output=(
                "CPU utilization for five seconds: 12%/1%; "
                "one minute: 8%; five minutes: 5%"
            ),
        )


class ProphylaxisServiceTests(unittest.TestCase):
    def service(self, devices, *, services=None, openbao=None, backend=None):
        return ProphylaxisService(
            FakeNetBox(devices, services),
            openbao or FakeOpenBao(),
            backend=backend or FakeBackend(),
        )

    def test_multiselect_values_support_api_and_legacy_shapes(self):
        self.assertEqual(
            selected_check_values(
                [
                    {"value": "cpu_utilization", "label": "CPU utilization"},
                    "uptime",
                ]
            ),
            {"cpu_utilization", "uptime"},
        )

    def test_device_becomes_vendor_neutral_target(self):
        target = device_target(netbox_device())
        self.assertEqual(target.id, 10)
        self.assertEqual(target.host, "10.10.10.1")
        self.assertEqual(target.platform, "cisco-ios-xe")
        self.assertEqual(target.vendor, "cisco")

    def test_cpu_flow_reuses_openbao_and_delegates_to_ansible(self):
        openbao = FakeOpenBao()
        backend = FakeBackend()
        service = self.service([netbox_device()], openbao=openbao, backend=backend)

        result = service.collect_cpu(10)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.check, CheckId.CPU_UTILIZATION)
        self.assertEqual(result.values.current_percent, 12.0)
        self.assertEqual(openbao.calls, [("cisco", "default", "ssh")])
        self.assertEqual(backend.calls[0]["host"], "10.10.10.1")
        self.assertEqual(backend.calls[0]["port"], 22)
        self.assertNotIn("password", result.model_dump_json())

    def test_fortios_uses_api_token_authentication_and_structured_data(self):
        openbao = FakeOpenBao()
        backend = FakeBackend()
        service = self.service(
            [netbox_device("fortios")],
            services=[{"name": "fortios-api", "port_mappings": ["tcp/444"]}],
            openbao=openbao,
            backend=backend,
        )

        result = service.collect_cpu(10)

        self.assertEqual(result.values.current_percent, 12.0)
        self.assertEqual(openbao.calls, [("fortinet", "default", "api_token")])
        self.assertEqual(backend.calls[0]["port"], 444)
        self.assertIn(
            (
                "/api/ipam/services/",
                {"device_id": 10, "name": "fortios-api"},
            ),
            service.netbox.calls,
        )

    def test_fortios_without_named_service_uses_https_default(self):
        backend = FakeBackend()

        result = self.service(
            [netbox_device("fortios")],
            backend=backend,
        ).collect_cpu(10)

        self.assertEqual(result.status, "ok")
        self.assertEqual(backend.calls[0]["port"], 443)

    def test_invalid_or_ambiguous_fortios_service_stops_before_secret_lookup(self):
        cases = (
            ([{"port_mappings": ["udp/444"]}], "invalid_service_port"),
            ([{"port_mappings": ["tcp/443", "tcp/444"]}], "ambiguous_service_port"),
            (
                [
                    {"port_mappings": ["tcp/443"]},
                    {"port_mappings": ["tcp/444"]},
                ],
                "ambiguous_service_port",
            ),
        )
        for services, expected in cases:
            with self.subTest(expected=expected):
                openbao = FakeOpenBao()
                backend = FakeBackend()

                result = self.service(
                    [netbox_device("fortios")],
                    services=services,
                    openbao=openbao,
                    backend=backend,
                ).collect_cpu(10)

                self.assertEqual(result.error_code, expected)
                self.assertEqual(openbao.calls, [])
                self.assertEqual(backend.calls, [])

    def test_legacy_cpu_choice_remains_accepted_during_migration(self):
        device = netbox_device(
            checks=[{"value": "CPU utilization", "label": "CPU utilization"}]
        )
        self.assertEqual(self.service([device]).collect_cpu(10).status, "ok")

    def test_platform_is_rejected_before_secret_lookup(self):
        openbao = FakeOpenBao()
        result = self.service(
            [netbox_device("unsupported")],
            openbao=openbao,
        ).collect_cpu(10)

        self.assertEqual(result.error_code, "unsupported_platform")
        self.assertEqual(openbao.calls, [])

    def test_execution_and_secret_errors_are_normalized(self):
        execution = self.service(
            [netbox_device()],
            backend=FakeBackend(AuthenticationFailedError()),
        ).collect_cpu(10)
        secret = self.service(
            [netbox_device()],
            openbao=FakeOpenBao(
                DeviceValidationError(
                    "sensitive",
                    code="credential_not_found",
                    category="error",
                )
            ),
        ).collect_cpu(10)

        self.assertEqual(execution.error_code, "authentication_failed")
        self.assertEqual(secret.error_code, "credential_not_found")

    def test_existing_credential_path_is_preserved(self):
        self.assertEqual(
            credential_path("cisco", "default"),
            "devices/credentials/cisco/default/admin",
        )


if __name__ == "__main__":
    unittest.main()
