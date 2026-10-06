import logging
import unittest
from pathlib import Path

from atlas.device_credentials import DeviceValidationError, credential_path
from atlas.network.base import SSHTimeouts
from atlas.network.errors import AuthenticationFailedError
from atlas.network.registry import DriverRegistry
from atlas.prophylaxis.errors import (
    MissingCredentialProfileError,
    MissingManufacturerError,
    MissingPlatformError,
    MissingPrimaryIPError,
)
from atlas.prophylaxis.models import CPUUtilizationValues
from atlas.prophylaxis.service import ProphylaxisService, device_target


def netbox_device():
    return {
        "id": 10,
        "name": "SW-CORE-01",
        "primary_ip4": {"address": "10.10.10.1/32"},
        "primary_ip6": None,
        "platform": {"slug": "cisco-ios-xe"},
        "device_type": {"manufacturer": {"slug": "cisco"}},
        "custom_fields": {
            "credential_profile": "cisco-default",
            "profylaxia_enabled": True,
        },
    }


class FakeNetBox:
    def __init__(self, devices):
        self.devices = devices
        self.calls = []

    def get_all(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.devices


class FakeOpenBao:
    def __init__(self, credentials=None, error=None):
        self.credentials = credentials or {
            "username": "fixture-user",
            "password": "fixture-password",
        }
        self.error = error
        self.calls = []

    def get_device_credentials(self, vendor, profile):
        self.calls.append((vendor, profile))
        if self.error:
            raise self.error
        return self.credentials


class FakeDriver:
    def __init__(self, values=None, error=None):
        self.values = values or CPUUtilizationValues(current_percent=12)
        self.error = error

    def get_cpu_utilization(self):
        if self.error:
            raise self.error
        return self.values


class CapturingRegistry:
    def __init__(self, driver):
        self.driver = driver
        self.calls = []

    def driver_class(self, platform):
        self.platform = platform
        return self.create

    def create(self, context, transport):
        self.calls.append((context, transport))
        return self.driver


class DeviceTargetTests(unittest.TestCase):
    def test_netbox_device_becomes_vendor_neutral_target(self):
        target = device_target(netbox_device())
        self.assertEqual(target.name, "SW-CORE-01")
        self.assertEqual(target.host, "10.10.10.1")
        self.assertEqual(target.platform, "cisco-ios-xe")
        self.assertEqual(target.vendor, "cisco")
        self.assertEqual(target.credential_profile, "cisco-default")

    def test_ipv6_prefix_is_removed(self):
        device = netbox_device()
        device["primary_ip4"] = None
        device["primary_ip6"] = {"address": "2001:db8::10/64"}
        self.assertEqual(device_target(device).host, "2001:db8::10")

    def test_missing_required_fields_are_explicit(self):
        cases = (
            ("primary_ip4", None, MissingPrimaryIPError),
            ("platform", None, MissingPlatformError),
            (
                "custom_fields",
                {"profylaxia_enabled": True},
                MissingCredentialProfileError,
            ),
            ("device_type", {}, MissingManufacturerError),
        )
        for field, value, error in cases:
            with self.subTest(field=field):
                device = netbox_device()
                device[field] = value
                with self.assertRaises(error):
                    device_target(device)

    def test_openbao_path_reuses_existing_vendor_profile_contract(self):
        self.assertEqual(
            credential_path("cisco", "cisco-default"),
            "devices/credentials/cisco/cisco-default/admin",
        )


class ProphylaxisServiceTests(unittest.TestCase):
    def service(self, netbox, openbao, registry):
        return ProphylaxisService(
            netbox,
            openbao,
            known_hosts_file=Path("/run/atlas/ssh/known_hosts"),
            strict_host_keys=False,
            timeouts=SSHTimeouts(2, 3, 4),
            registry=registry,
            transport=object(),
        )

    def test_cpu_flow_resolves_device_secret_context_driver_and_result(self):
        netbox = FakeNetBox([netbox_device()])
        openbao = FakeOpenBao()
        registry = CapturingRegistry(FakeDriver())

        result = self.service(netbox, openbao, registry).collect_cpu("SW-CORE-01")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.check, "CPU utilization")
        self.assertEqual(result.values.current_percent, 12.0)
        self.assertEqual(
            netbox.calls,
            [("/api/dcim/devices/", {"name": "SW-CORE-01"})],
        )
        self.assertEqual(openbao.calls, [("cisco", "cisco-default")])
        self.assertEqual(registry.platform, "cisco-ios-xe")
        context = registry.calls[0][0]
        self.assertEqual(context.host, "10.10.10.1")
        self.assertEqual(context.platform, "cisco-ios-xe")
        self.assertFalse(context.strict_host_keys)

    def test_integer_identifier_uses_netbox_id_filter(self):
        netbox = FakeNetBox([netbox_device()])
        service = self.service(
            netbox,
            FakeOpenBao(),
            CapturingRegistry(FakeDriver()),
        )
        service.collect_cpu(10)
        self.assertEqual(netbox.calls[0][1], {"id": 10})

    def test_unsupported_platform_fails_before_secret_lookup(self):
        device = netbox_device()
        device["platform"] = {"slug": "unsupported"}
        openbao = FakeOpenBao()

        result = self.service(
            FakeNetBox([device]),
            openbao,
            DriverRegistry(),
        ).collect_cpu("SW-CORE-01")

        self.assertEqual(result.error, "unsupported_platform")
        self.assertEqual(openbao.calls, [])

    def test_resolution_and_collection_errors_are_normalized(self):
        cases = (
            (FakeNetBox([]), FakeOpenBao(), FakeDriver(), "device_not_found"),
            (
                FakeNetBox([netbox_device()]),
                FakeOpenBao(
                    error=DeviceValidationError(
                        "sensitive",
                        code="credential_not_found",
                        category="error",
                    )
                ),
                FakeDriver(),
                "credential_not_found",
            ),
            (
                FakeNetBox([netbox_device()]),
                FakeOpenBao(),
                FakeDriver(error=AuthenticationFailedError()),
                "authentication_failed",
            ),
        )
        for netbox, openbao, driver, code in cases:
            with self.subTest(code=code):
                result = self.service(
                    netbox,
                    openbao,
                    CapturingRegistry(driver),
                ).collect_cpu("SW-CORE-01")
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error, code)
                self.assertIsNone(result.values)

    def test_logs_and_result_do_not_contain_credentials(self):
        secret = "do-not-log-password"
        openbao = FakeOpenBao(
            credentials={"username": "admin", "password": secret}
        )
        service = self.service(
            FakeNetBox([netbox_device()]),
            openbao,
            CapturingRegistry(FakeDriver(error=AuthenticationFailedError())),
        )

        with self.assertLogs("atlas.prophylaxis", level=logging.WARNING) as logs:
            result = service.collect_cpu("SW-CORE-01")

        output = "\n".join(logs.output) + result.model_dump_json()
        self.assertNotIn(secret, output)
        self.assertNotIn("SecretID", output)
        self.assertNotIn("OpenBao token", output)


if __name__ == "__main__":
    unittest.main()
