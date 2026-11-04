import base64
import tempfile
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.vpn.import_service import VPNProfileImportService
from test_vpn_api import FakeVPNService, OPERATOR_TOKEN
from test_vpn_profile_import import Registry, Writer, profile


class VPNProfileImportAPITests(unittest.TestCase):
    def setUp(self):
        self.token_file = tempfile.NamedTemporaryFile(mode='w+')
        self.token_file.write(OPERATOR_TOKEN)
        self.token_file.flush()
        self.writer = Writer()
        service = VPNProfileImportService(Registry(), self.writer)
        settings = Settings(environment='test', log_level='CRITICAL',
            monitoring_operator_token_file=self.token_file.name, oxidized_enabled=False,
            zabbix_enabled=False, _env_file=None)
        self.client = TestClient(create_app(settings, dependencies=SimpleNamespace(
            vpn=FakeVPNService(), vpn_import=service, openbao=None)), raise_server_exceptions=False)
        self.headers = {'Authorization': f'Bearer {OPERATOR_TOKEN}'}
        self.body = {'format': 'macos-plist', 'profile_base64': base64.b64encode(profile()).decode(),
            'profile_name': 'office', 'username': 'user@example.test', 'password': 'example-pass',
            'connection_id': 'customer-vpn', 'name': 'Customer VPN', 'tenant_id': 1,
            'management_vrf_id': 30, 'allowed_prefixes': ['192.0.2.0/24']}

    def tearDown(self):
        self.token_file.close()

    def test_secret_bound_plan_and_apply(self):
        endpoint = '/api/v1/vpn/forticlient/'
        denied = self.client.post(endpoint + 'plan', json=self.body)
        self.assertEqual(denied.status_code, 401)
        preview = self.client.post(endpoint + 'plan', json=self.body, headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        for secret in ('example-pass', 'example-psk', self.body['profile_base64']):
            self.assertNotIn(secret, preview.text)
        plan_hash = preview.json()['plan_hash']
        changed = {**self.body, 'password': 'changed-password', 'plan_hash': plan_hash}
        stale = self.client.post(endpoint + 'apply', json=changed, headers=self.headers)
        self.assertEqual(stale.status_code, 409)
        self.assertIsNone(self.writer.secret)
        applied = self.client.post(endpoint + 'apply', json={**self.body, 'plan_hash': plan_hash},
                                   headers=self.headers)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertNotIn('example-pass', applied.text)
        self.assertEqual(self.writer.secret['password'], 'example-pass')
        self.assertEqual(applied.json()['runtime_status'], 'not_deployed')

    def test_validation_does_not_echo_credentials(self):
        body = {**self.body, 'tenant_id': -1, 'password': 'must-not-appear'}
        response = self.client.post('/api/v1/vpn/forticlient/plan', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('must-not-appear', response.text)
        self.assertIsNone(self.writer.secret)


if __name__ == '__main__':
    unittest.main()
