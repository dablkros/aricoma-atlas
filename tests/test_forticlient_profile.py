import hashlib
import hmac
import plistlib
import unittest

from atlas.vpn.forticlient_profile import (
    FortiClientProfileError, decode_macos_enc, decode_windows_encx,
    parse_forticlient_profile,
)


def fixture():
    return {"Profiles": {"demo": {
        "Server": "vpn.example.test", "NetworkID": 11, "IKEVersion": 2,
        "UdpPort": 4500, "AuthMethod": 0, "SSOEnabled": 0, "EnableXAuth": 1,
        "Phase1Proposals": "AES256GCM|PRFSHA512", "Phase1DHGroup": 21,
        "Phase2Proposals": "AES256GCM|NONE", "Phase2DHGroup": 21,
        "Phase2PFSEnabled": 1, "AuthInfo": "Enc abcd",
    }}}


class FortiClientProfileTests(unittest.TestCase):
    def test_binary_plist_extracts_only_selected_profile(self):
        data = plistlib.dumps(fixture(), fmt=plistlib.FMT_BINARY)
        profile = parse_forticlient_profile(data, 'demo', format='macos-plist')
        self.assertEqual(profile.network_id, 11)
        self.assertEqual(profile.gateway_port, 4500)
        self.assertNotIn(profile.encrypted_auth_info, repr(profile))

    def test_xml_rejects_entity_expansion(self):
        xml = b'<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>'
        with self.assertRaises(FortiClientProfileError):
            parse_forticlient_profile(xml, 'demo', format='windows-xml')

    def test_windows_profile_preserves_encx_as_unsupported(self):
        xml = b'''<root><connection><name>demo</name><ike_settings>
          <server>vpn.example.test</server><networkid>11</networkid>
          <version>2</version><udp_port>500</udp_port><localid>DEMO</localid>
          <sso_enabled>0</sso_enabled><xauth><enabled>1</enabled></xauth>
          <authentication_method>Preshared Key</authentication_method>
          <auth_data><preshared_key>EncX 1234</preshared_key></auth_data>
          <dhgroup>21;</dhgroup><proposals><proposal>AES256|SHA256</proposal></proposals>
          </ike_settings><ipsec_settings><dhgroup>21</dhgroup><pfs>1</pfs>
          <proposals><proposal>AES256|SHA256</proposal></proposals>
          </ipsec_settings></connection></root>'''
        profile = parse_forticlient_profile(xml, 'demo', format='windows-xml')
        self.assertEqual(profile.local_id, 'DEMO')
        self.assertEqual(profile.phase1_dh_group, 21)
        with self.assertRaisesRegex(FortiClientProfileError, 'unsupported_encrypted_secret_format'):
            decode_macos_enc(profile.encrypted_auth_info)

    def test_reject_encx_and_corruption_without_leaking_data(self):
        with self.assertRaisesRegex(FortiClientProfileError, 'unsupported_encrypted_secret_format'):
            decode_macos_enc('EncX 1234')
        with self.assertRaisesRegex(FortiClientProfileError, 'encrypted_secret_integrity_failed'):
            decode_macos_enc('Enc ' + '00' * 30)

    def test_synthetic_authenticated_enc_roundtrip_and_tamper(self):
        salt = bytes(range(12))
        key = hashlib.pbkdf2_hmac('sha1', b'fc_1A2Brown3Fox4Jumped5Over6A7Lazy8Dog', salt, 1000, 50)
        # Generate an authenticated synthetic ciphertext using the independent CLI.
        import subprocess
        stream = subprocess.run(['openssl', 'enc', '-aes-192-ecb', '-nopad', '-K', key[:24].hex()],
                                input=(1).to_bytes(16, 'little'), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=True).stdout
        plaintext = b'test-psk\0'
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
        tag = hmac.new(key[24:48], ciphertext, hashlib.sha1).digest()[:10]
        encoded = 'Enc ' + (salt + key[48:] + ciphertext + tag).hex()
        self.assertEqual(decode_macos_enc(encoded), b'test-psk')
        bad = encoded[:-2] + ('00' if encoded[-2:] != '00' else '01')
        with self.assertRaisesRegex(FortiClientProfileError, 'encrypted_secret_integrity_failed'):
            decode_macos_enc(bad)

    def test_encx_integrity_with_export_password(self):
        import subprocess
        for size in (16, 24, 32):
            with self.subTest(size=size):
                salt = bytes(range(size // 2))
                key = hashlib.pbkdf2_hmac('sha1', b'example-export-password', salt, 1000, 2 * size + 2)
                stream = subprocess.run(['openssl', 'enc', f'-aes-{size * 8}-ecb', '-nopad', '-K', key[:size].hex()],
                                        input=(1).to_bytes(16, 'little'), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, check=True).stdout
                ciphertext = bytes(a ^ b for a, b in zip(b'test-psk\0', stream))
                mac = hmac.new(key[size:2*size], ciphertext, hashlib.sha1).digest()[:10]
                data = 'EncX ' + (salt + key[2*size:] + ciphertext + mac).hex()
                self.assertEqual(decode_windows_encx(data, 'example-export-password'), b'test-psk')
                with self.assertRaisesRegex(FortiClientProfileError, 'encrypted_secret_integrity_failed'):
                    decode_windows_encx(data, 'incorrect')


if __name__ == '__main__':
    unittest.main()
