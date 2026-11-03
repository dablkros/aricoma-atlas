import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import plistlib
import unittest
from uuid import UUID

from pydantic import SecretStr

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from atlas.services.errors import DependencyError
from atlas.vpn.import_models import ProfileImportRequest, ProfileInspectRequest
from atlas.vpn.import_service import VPNImportError, VPNProfileImportService, inspect_profile
from atlas.vpn.models import VPNConnectionRecord
from atlas.vpn.openvpn_profile import render_openvpn_runtime_config
from atlas.vpn.service import VPNRegistryError


def profile():
    salt = bytes(range(12))
    key = hashlib.pbkdf2_hmac('sha1', b'fc_1A2Brown3Fox4Jumped5Over6A7Lazy8Dog', salt, 1000, 50)
    enc = Cipher(algorithms.AES(key[:24]), modes.ECB()).encryptor()
    stream = enc.update((1).to_bytes(16, 'little')) + enc.finalize()
    ciphertext = bytes(a ^ b for a, b in zip(b'example-psk\0', stream))
    tag = hmac.new(key[24:48], ciphertext, hashlib.sha1).digest()[:10]
    return plistlib.dumps({'Profiles': {'office': {
        'Server': 'vpn.example.test', 'NetworkID': 11, 'IKEVersion': 2,
        'UdpPort': 4500, 'AuthMethod': 0, 'SSOEnabled': 0, 'EnableXAuth': 1,
        'Phase1Proposals': 'AES256GCM|PRFSHA512', 'Phase1DHGroup': 21,
        'Phase2Proposals': 'AES256GCM|NONE', 'Phase2DHGroup': 21,
        'Phase2PFSEnabled': 1, 'AuthInfo': 'Enc ' + (salt + key[48:] + ciphertext + tag).hex(),
    }}})


def request():
    return ProfileImportRequest(format='macos-plist', profile_base64=base64.b64encode(profile()).decode(),
        profile_name='office', username='user@example.test', password='example-pass',
        connection_id='customer-vpn', name='Customer VPN', tenant_id=1,
        management_vrf_id=30, allowed_prefixes=['192.0.2.0/24'])


def openvpn_profile(extra=''):
    return (f'''client
dev tun
proto udp
remote vpn.example.test 1194
auth-user-pass
route 192.0.2.0 255.255.255.0
{extra}<ca>
-----BEGIN CERTIFICATE-----
Y2E=
-----END CERTIFICATE-----
</ca>
<cert>
-----BEGIN CERTIFICATE-----
Y2VydA==
-----END CERTIFICATE-----
</cert>
<key>
-----BEGIN PRIVATE KEY-----
a2V5
-----END PRIVATE KEY-----
</key>
''').encode()


class Registry:
    def __init__(self): self.value = None
    def _validate_references(self, r):
        if r.tenant_id != 1 or r.management_vrf_id != 30:
            raise VPNRegistryError('management_vrf_tenant_mismatch', 409)
    def get(self, name):
        if self.value is None: raise VPNRegistryError('connection_not_found', 404)
        return self.value
    def create(self, value):
        self._validate_references(value)
        self.value = VPNConnectionRecord(**value.model_dump(), id=UUID(int=1), tenant_slug='customer',
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        return self.value
    def replace(self, value):
        self._validate_references(value)
        if self.value is None: raise VPNRegistryError('connection_not_found', 404)
        self.value = VPNConnectionRecord(**value.model_dump(), id=self.value.id,
            tenant_slug='customer', created_at=self.value.created_at,
            updated_at=datetime.now(timezone.utc))
        return self.value
    def patch(self, name, changes):
        if self.value is None or self.value.connection_id != name:
            raise VPNRegistryError('connection_not_found', 404)
        values = self.value.model_dump()
        values.update(changes.model_dump(exclude_unset=True))
        self.value = VPNConnectionRecord(**values)
        return self.value


class Writer:
    def __init__(self): self.secret = None; self.fail = False
    def read_secret_with_metadata(self, path):
        if self.secret is None: raise DependencyError('secret_not_found')
        return self.secret, {'version': 1}
    def write_secret(self, path, value, *, cas):
        if self.fail: raise DependencyError('connection_failed')
        expected = 0 if self.secret is None else 1
        if cas != expected: raise DependencyError('conflict')
        self.secret = json.loads(json.dumps(value))


class VPNImportTests(unittest.TestCase):
    def setUp(self):
        self.registry, self.writer = Registry(), Writer()
        self.service = VPNProfileImportService(self.registry, self.writer)

    def test_inspect_and_import_are_secret_free_and_idempotent(self):
        body = request()
        names = inspect_profile(ProfileInspectRequest(format=body.format, profile_base64=body.profile_base64))
        self.assertEqual(names, {'profiles': ['office']})
        plan = self.service.plan(body)
        self.assertNotIn('example-pass', str(plan))
        self.assertNotIn('example-psk', str(plan))
        self.assertEqual(plan['registry_action'], 'create')
        self.assertEqual(self.service.apply(body, plan)['status'], 'saved')
        again = self.service.plan(body)
        self.assertEqual((again['registry_action'], again['secret_action']), ('unchanged', 'unchanged'))
        self.assertEqual(self.writer.secret['tenant_id'], 1)
        self.assertNotIn('profile_type', self.writer.secret)
        self.assertNotIn('encrypted_auth_info', self.writer.secret['profile'])

    def test_openvpn_profile_is_validated_and_stored_only_as_secret(self):
        encoded = base64.b64encode(openvpn_profile()).decode()
        inspected = inspect_profile(ProfileInspectRequest(
            format='openvpn-config', profile_base64=encoded,
            source_name='customer-access.ovpn',
        ))
        self.assertEqual(inspected, {'profiles': ['customer-access']})
        body = request().model_copy(update={
            'format': 'openvpn-config', 'profile_base64': SecretStr(encoded),
            'source_name': 'customer-access.ovpn',
            'profile_name': 'customer-access',
        })
        plan = self.service.plan(body)
        self.assertEqual((plan['profile_type'], plan['port']), ('openvpn', 1194))
        self.assertNotIn('PRIVATE KEY', str(plan))
        self.service.apply(body, plan)
        self.assertEqual((self.registry.value.driver, self.registry.value.vendor),
                         ('openvpn', 'generic'))
        self.assertIn('PRIVATE KEY', self.writer.secret['config'])

    def test_openvpn_rejects_scripts_and_external_auth_files(self):
        for profile in (
            openvpn_profile('up /tmp/script\n'),
            openvpn_profile().replace(b'auth-user-pass\n', b'auth-user-pass /tmp/auth\n'),
        ):
            body = ProfileInspectRequest(
                format='openvpn-config',
                profile_base64=base64.b64encode(profile).decode(),
            )
            with self.assertRaises(VPNImportError):
                inspect_profile(body)

    def test_openvpn_runtime_adds_exact_routes_for_selected_devices(self):
        rendered = render_openvpn_runtime_config(
            openvpn_profile().decode(),
            "/run/atlas-vpn-worker/auth.txt",
            ("10.1.248.20", "10.1.248.21", "10.1.248.20"),
        )

        self.assertIn(
            "route 10.1.248.20 255.255.255.255 vpn_gateway",
            rendered,
        )
        self.assertIn(
            "route 10.1.248.21 255.255.255.255 vpn_gateway",
            rendered,
        )
        self.assertEqual(rendered.count("route 10.1.248.20 "), 1)

    def test_windows_encx_requires_valid_export_password(self):
        salt = bytes(range(12))
        key = hashlib.pbkdf2_hmac('sha1', b'export-pass', salt, 1000, 50)
        enc = Cipher(algorithms.AES(key[:24]), modes.ECB()).encryptor()
        stream = enc.update((1).to_bytes(16, 'little')) + enc.finalize()
        ciphertext = bytes(a ^ b for a, b in zip(b'example-psk\0', stream))
        tag = hmac.new(key[24:48], ciphertext, hashlib.sha1).digest()[:10]
        encrypted = 'EncX ' + (salt + key[48:] + ciphertext + tag).hex()
        xml = (f'<root><connection><name>office</name><ike_settings>'
               f'<server>vpn.example.test</server><networkid>11</networkid><version>2</version>'
               f'<udp_port>500</udp_port><localid>OFFICE</localid><sso_enabled>0</sso_enabled>'
               f'<xauth><enabled>1</enabled></xauth><authentication_method>Preshared Key</authentication_method>'
               f'<auth_data><preshared_key>{encrypted}</preshared_key></auth_data><dhgroup>21</dhgroup>'
               f'<proposals><proposal>AES256|SHA256</proposal></proposals></ike_settings>'
               f'<ipsec_settings><dhgroup>21</dhgroup><pfs>1</pfs>'
               f'<proposals><proposal>AES256|SHA256</proposal></proposals></ipsec_settings>'
               f'</connection></root>').encode()
        body = request().model_copy(update={'format': 'windows-xml',
            'profile_base64': SecretStr(base64.b64encode(xml).decode()),
            'export_password': SecretStr('wrong')})
        with self.assertRaisesRegex(VPNImportError, 'encrypted_secret_integrity_failed'):
            self.service.plan(body)
        self.assertIsNone(self.writer.secret)
        body = body.model_copy(update={'export_password': SecretStr('export-pass')})
        self.service.apply(body, self.service.plan(body))
        self.assertEqual(base64.b64decode(self.writer.secret['psk_base64']), b'example-psk')
        self.assertNotIn('export_password', self.writer.secret)

    def test_windows_classic_ssl_vpn_import_needs_no_psk(self):
        xml = (b'<forticlient_configuration><vpn><sslvpn><connections><connection>'
               b'<name>office-ssl</name><server>ssl.example.test:10443</server>'
               b'<realm>staff</realm><sso_enabled>0</sso_enabled>'
               b'</connection></connections></sslvpn></vpn></forticlient_configuration>')
        body = request().model_copy(update={
            'format': 'windows-xml',
            'profile_base64': SecretStr(base64.b64encode(xml).decode()),
            'profile_name': 'office-ssl', 'export_password': None,
        })
        plan = self.service.plan(body)
        self.assertEqual((plan['profile_type'], plan['network_id'], plan['port']),
                         ('ssl-vpn', None, 10443))
        self.service.apply(body, plan)
        self.assertEqual((self.registry.value.driver, self.registry.value.auth_method),
                         ('openfortivpn', 'username_password'))
        self.assertNotIn('psk_base64', self.writer.secret)
        self.assertEqual(self.writer.secret['profile']['realm'], 'staff')

    def test_push_mfa_is_explicit_and_can_update_existing_registry_record(self):
        body = request()
        self.service.apply(body, self.service.plan(body))

        mfa_body = body.model_copy(update={'external_mfa': True})
        plan = self.service.plan(mfa_body)

        self.assertEqual(plan['registry_action'], 'update')
        self.assertTrue(plan['external_mfa'])
        self.service.apply(mfa_body, plan)
        self.assertEqual(self.registry.value.auth_method, 'external_mfa')

    def test_windows_ssl_sso_is_explicitly_rejected(self):
        xml = (b'<root><connection><name>sso</name><server>ssl.example.test</server>'
               b'<sso_enabled>1</sso_enabled></connection></root>')
        body = request().model_copy(update={
            'format': 'windows-xml',
            'profile_base64': SecretStr(base64.b64encode(xml).decode()),
            'profile_name': 'sso',
        })
        with self.assertRaisesRegex(VPNImportError, 'unsupported_ssl_sso'):
            self.service.plan(body)

    def test_import_can_use_site_membership_without_entering_prefixes(self):
        body = request().model_copy(update={'allowed_prefixes': []})
        plan = self.service.plan(body)
        self.assertEqual(plan['allowed_prefixes'], [])
        self.service.apply(body, plan)
        self.assertEqual(self.registry.value.allowed_prefixes, [])
        self.assertEqual(self.service.plan(body)['registry_action'], 'unchanged')

    def test_partial_write_can_be_retried_without_overwrite(self):
        body = request(); plan = self.service.plan(body); self.writer.fail = True
        with self.assertRaisesRegex(VPNImportError, 'vpn_import_partial'):
            self.service.apply(body, plan)
        self.writer.fail = False
        retry = self.service.plan(body)
        self.assertEqual((retry['registry_action'], retry['secret_action']), ('unchanged', 'create'))
        self.assertEqual(self.service.apply(body, retry)['status'], 'saved')

    def test_conflicting_secret_and_customer_are_rejected(self):
        body = request(); self.writer.secret = {'password': 'different'}
        with self.assertRaisesRegex(VPNImportError, 'vpn_secret_conflict'):
            self.service.plan(body)
        self.writer.secret = None
        with self.assertRaisesRegex(VPNRegistryError, 'management_vrf_tenant_mismatch'):
            self.service.plan(body.model_copy(update={'tenant_id': 2}))

    def test_explicit_replace_updates_registry_and_secret_with_cas(self):
        body = request()
        self.service.apply(body, self.service.plan(body))
        replacement = body.model_copy(update={
            'name': 'Replacement VPN',
            'password': SecretStr('replacement-pass'),
            'replace_existing': True,
        })

        plan = self.service.plan(replacement)

        self.assertEqual((plan['registry_action'], plan['secret_action']),
                         ('replace', 'replace'))
        self.service.apply(replacement, plan)
        self.assertEqual(self.registry.value.name, 'Replacement VPN')
        self.assertEqual(self.writer.secret['password'], 'replacement-pass')

    def test_stale_plan_does_not_write(self):
        body = request(); plan = self.service.plan(body); self.registry.create(self.service._values(body)[0])
        with self.assertRaisesRegex(VPNImportError, 'vpn_import_plan_stale'):
            self.service.apply(body, plan)
        self.assertIsNone(self.writer.secret)


if __name__ == '__main__': unittest.main()
