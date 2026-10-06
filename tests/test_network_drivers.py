import tempfile
import unittest
from pathlib import Path

import paramiko
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
)

from atlas.network.base import ConnectionContext, SSHTimeouts
from atlas.network.drivers.cisco_ios import (
    CiscoIOSDriver,
    parse_cpu_output as parse_cisco_ios_cpu,
)
from atlas.network.drivers.cisco_smb import (
    CiscoSMBDriver,
    parse_cpu_output as parse_cisco_smb_cpu,
)
from atlas.network.drivers.fortios import (
    FortiOSDriver,
    parse_cpu_output as parse_fortios_cpu,
)
from atlas.network.errors import ParseFailedError, UnsupportedPlatformError
from atlas.network.registry import DriverRegistry
from atlas.network.transport import NetmikoTransport


FIXTURES = Path(__file__).parent / "fixtures/prophylaxis"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def context(known_hosts, *, strict=True):
    return ConnectionContext(
        device_name="fixture-device",
        host="192.0.2.10",
        platform="cisco-ios-xe",
        username="fixture-user",
        password="fixture-password",
        enable_password="fixture-enable",
        strict_host_keys=strict,
        known_hosts_file=known_hosts,
        timeouts=SSHTimeouts(2.0, 3.0, 4.0),
    )


class DriverRegistryTests(unittest.TestCase):
    def test_explicit_platform_mapping(self):
        registry = DriverRegistry()
        self.assertIs(registry.driver_class("cisco-ios"), CiscoIOSDriver)
        self.assertIs(registry.driver_class("cisco-ios-xe"), CiscoIOSDriver)
        self.assertIs(registry.driver_class("cisco-cbs"), CiscoSMBDriver)
        self.assertIs(registry.driver_class("fortios"), FortiOSDriver)

    def test_unknown_platform_has_no_fallback(self):
        with self.assertRaises(UnsupportedPlatformError) as raised:
            DriverRegistry().driver_class("unknown")
        self.assertEqual(raised.exception.code, "unsupported_platform")


class CPUParserTests(unittest.TestCase):
    def test_cisco_ios_fixture(self):
        values = parse_cisco_ios_cpu(fixture("cisco_ios_cpu.txt"))
        self.assertEqual(values.current_percent, 8.0)
        self.assertEqual(values.one_minute_percent, 6.0)
        self.assertEqual(values.five_minute_percent, 5.0)

    def test_cisco_cbs_fixture(self):
        values = parse_cisco_smb_cpu(fixture("cisco_cbs_cpu.txt"))
        self.assertEqual(values.current_percent, 5.0)
        self.assertEqual(values.one_minute_percent, 3.0)
        self.assertEqual(values.five_minute_percent, 3.0)

    def test_fortios_fixture_returns_busy_cpu(self):
        values = parse_fortios_cpu(fixture("fortios_cpu.txt"))
        self.assertEqual(values.current_percent, 29.0)
        self.assertIsNone(values.one_minute_percent)
        self.assertIsNone(values.five_minute_percent)

    def test_malformed_output_is_not_reported_as_zero(self):
        for parser in (
            parse_cisco_ios_cpu,
            parse_cisco_smb_cpu,
            parse_fortios_cpu,
        ):
            with self.subTest(parser=parser.__module__):
                with self.assertRaises(ParseFailedError) as raised:
                    parser(fixture("malformed_cpu.txt"))
                self.assertEqual(raised.exception.code, "parse_failed")


class RecordingTransport:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def run_command(self, context, **kwargs):
        self.calls.append((context, kwargs))
        return self.output


class DriverCommandTests(unittest.TestCase):
    def test_each_driver_uses_its_reviewed_command_and_transport_adapter(self):
        cases = (
            (
                CiscoIOSDriver,
                "cisco_ios_cpu.txt",
                "cisco_ios",
                "show processes cpu",
            ),
            (
                CiscoSMBDriver,
                "cisco_cbs_cpu.txt",
                "cisco_s300",
                "show cpu utilization",
            ),
            (
                FortiOSDriver,
                "fortios_cpu.txt",
                "fortinet",
                "get system performance status",
            ),
        )
        for driver_class, fixture_name, device_type, command in cases:
            with self.subTest(driver=driver_class.__name__):
                transport = RecordingTransport(fixture(fixture_name))
                driver = driver_class(
                    context(Path("/unused"), strict=False),
                    transport,
                )

                driver.get_cpu_utilization()

                self.assertEqual(
                    transport.calls[0][1]["device_type"],
                    device_type,
                )
                self.assertEqual(transport.calls[0][1]["command"], command)


class FakeConnection:
    def __init__(self, output="fixture output", command_error=None):
        self.output = output
        self.command_error = command_error
        self.commands = []
        self.enabled = False
        self.disconnected = False

    def enable(self):
        self.enabled = True

    def send_command(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if self.command_error:
            raise self.command_error
        return self.output

    def disconnect(self):
        self.disconnected = True


class QueueConnector:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.known_hosts = Path(self.temporary.name) / "known_hosts"
        self.known_hosts.write_text("fixture host key\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_strict_mode_uses_only_explicit_known_hosts_and_timeouts(self):
        connection = FakeConnection()
        connector = QueueConnector(connection)
        transport = NetmikoTransport(connector)

        output = transport.run_command(
            context(self.known_hosts),
            device_type="cisco_ios",
            command="show processes cpu",
            use_enable=True,
        )

        self.assertEqual(output, "fixture output")
        options = connector.calls[0]
        self.assertTrue(options["ssh_strict"])
        self.assertTrue(options["alt_host_keys"])
        self.assertFalse(options["system_host_keys"])
        self.assertFalse(options["allow_agent"])
        self.assertEqual(options["alt_key_file"], str(self.known_hosts))
        self.assertEqual(options["conn_timeout"], 2.0)
        self.assertEqual(options["auth_timeout"], 3.0)
        self.assertEqual(options["read_timeout_override"], 4.0)
        self.assertTrue(connection.enabled)
        self.assertTrue(connection.disconnected)
        self.assertEqual(
            connection.commands,
            [("show processes cpu", {"read_timeout": 4.0})],
        )

    def test_development_mode_does_not_require_known_hosts(self):
        missing = Path(self.temporary.name) / "missing"
        connector = QueueConnector(FakeConnection())

        NetmikoTransport(connector).run_command(
            context(missing, strict=False),
            device_type="fortinet",
            command="get system performance status",
        )

        options = connector.calls[0]
        self.assertFalse(options["ssh_strict"])
        self.assertFalse(options["alt_host_keys"])

    def test_transport_maps_authentication_timeout_and_command_errors(self):
        cases = (
            (NetmikoAuthenticationException("sensitive"), "authentication_failed"),
            (NetmikoTimeoutException("sensitive"), "connection_timeout"),
        )
        for exception, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(Exception, code):
                    NetmikoTransport(QueueConnector(exception)).run_command(
                        context(self.known_hosts),
                        device_type="cisco_ios",
                        command="show processes cpu",
                    )

        connection = FakeConnection(command_error=ReadTimeout("sensitive"))
        with self.assertRaisesRegex(Exception, "command_failed"):
            NetmikoTransport(QueueConnector(connection)).run_command(
                context(self.known_hosts),
                device_type="cisco_ios",
                command="show processes cpu",
            )

    def test_transport_distinguishes_unknown_and_changed_host_keys(self):
        unknown = paramiko.SSHException(
            "Server '192.0.2.10' not found in known_hosts"
        )
        got = paramiko.RSAKey.generate(1024)
        expected = paramiko.RSAKey.generate(1024)
        mismatch = paramiko.BadHostKeyException("192.0.2.10", got, expected)

        for exception, code in (
            (unknown, "host_key_unknown"),
            (mismatch, "host_key_mismatch"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(Exception, code):
                    NetmikoTransport(QueueConnector(exception)).run_command(
                        context(self.known_hosts),
                        device_type="cisco_ios",
                        command="show processes cpu",
                    )

    def test_context_repr_excludes_credentials(self):
        representation = repr(context(self.known_hosts))
        self.assertNotIn("fixture-user", representation)
        self.assertNotIn("fixture-password", representation)
        self.assertNotIn("fixture-enable", representation)


if __name__ == "__main__":
    unittest.main()
