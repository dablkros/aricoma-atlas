import base64
import plistlib
import unittest

from atlas.vpn.forticlient_profile import FortiClientProfileError, parse_forticlient_profile
from atlas.vpn.strongswan_config import VPNSecrets, render_fortinet_daemon_fragment, render_swanctl
from test_forticlient_profile import fixture


class StrongSwanConfigTests(unittest.TestCase):
    def setUp(self):
        self.profile = parse_forticlient_profile(plistlib.dumps(fixture()), 'demo', format='macos-plist')
        self.secrets = VPNSecrets('user@example.test', 'password\"\nvalue', b'psk\x00bytes')

    def test_sensitive_values_are_encoded_and_prefix_restricted(self):
        result = render_swanctl(self.profile, self.secrets, ('192.0.2.0/24',))
        self.assertIn('remote_ts = 192.0.2.0/24', result)
        self.assertIn('aes256gcm16-prfsha512-ecp521', result)
        self.assertNotIn(self.secrets.password, result)
        self.assertIn('0s' + base64.b64encode(self.secrets.psk).decode(), result)
        self.assertNotIn('password', repr(self.secrets))

    def test_rejects_invalid_prefixes_and_credentials(self):
        with self.assertRaises(FortiClientProfileError):
            render_swanctl(self.profile, self.secrets, ())
        with self.assertRaises(FortiClientProfileError):
            render_swanctl(self.profile, self.secrets, ('192.0.2.1/24',))
        with self.assertRaises(FortiClientProfileError):
            render_swanctl(self.profile, VPNSecrets('bad\nname', 'p', b'key'), ('192.0.2.0/24',))

    def test_network_id_is_explicit_per_worker(self):
        self.assertIn('atlas_fortinet_network_id = 11', render_fortinet_daemon_fragment(11))
        for value in (-1, 256, True, '11'):
            with self.subTest(value=value), self.assertRaises(FortiClientProfileError):
                render_fortinet_daemon_fragment(value)


if __name__ == '__main__':
    unittest.main()
