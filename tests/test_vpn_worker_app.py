from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from atlas.vpn.worker_app import (
    WorkerTestRequest, _profile, _render_ssl_config, _ssl_profile, settings, test_vpn,
)


def request():
    return WorkerTestRequest(
        connection_id="customer-vpn", tenant_id=1,
        secret_ref="vpn/connections/customer-vpn",
        gateway_host="vpn.example.test", gateway_port=500,
        driver="strongswan", protocol="ipsec-ikev2",
        target_ip="10.0.0.10", target_port=22,
    )


def secret():
    return {
        "schema_version": 1, "connection_id": "customer-vpn", "tenant_id": 1,
        "profile": {
            "name": "Customer", "gateway": "vpn.example.test", "network_id": 11,
            "ike_version": 2, "gateway_port": 500, "local_id": "CUSTOMER",
            "user_auth_enabled": True, "phase1_proposals": ["AES256|SHA256"],
            "phase1_dh_group": 21, "phase2_proposals": ["AES256|SHA256"],
            "phase2_dh_group": 21, "pfs": True,
        },
        "psk_base64": "cHNr", "username": "person", "password": "password",
    }


def ssl_request():
    return request().model_copy(update={
        "gateway_port": 443, "driver": "openfortivpn", "protocol": "ssl-vpn",
    })


def ssl_secret():
    return {
        "schema_version": 1, "profile_type": "ssl-vpn",
        "connection_id": "customer-vpn", "tenant_id": 1,
        "profile": {"name": "Customer SSL", "gateway": "vpn.example.test",
                    "gateway_port": 443, "realm": "staff", "sso_enabled": False},
        "username": "person@example.test", "password": "password",
    }


class VPNWorkerAppTests(unittest.TestCase):
    def test_secret_must_match_registry_metadata(self):
        value = secret()
        value["tenant_id"] = 2
        with self.assertRaisesRegex(RuntimeError, "secret_schema_invalid"):
            _profile(value, request())

    def test_profile_material_is_reconstructed_without_exposing_secret(self):
        profile, credentials = _profile(secret(), request())
        self.assertEqual(profile.network_id, 11)
        self.assertNotIn("psk", repr(credentials).lower())
        self.assertNotIn("password", repr(credentials).lower())

    def test_ssl_profile_renders_private_config_without_command_line_secret(self):
        profile, username, password = _ssl_profile(ssl_secret(), ssl_request())
        rendered = _render_ssl_config(profile, username, password)
        self.assertIn("host = vpn.example.test", rendered)
        self.assertIn("realm = staff", rendered)
        self.assertIn("password = password", rendered)

    def test_ssl_config_rejects_newline_injection(self):
        value = ssl_secret(); value["password"] = "secret\nset-dns = 1"
        with self.assertRaisesRegex(RuntimeError, "invalid_password"):
            _ssl_profile(value, ssl_request())

    def test_ssl_test_connects_target_and_always_runs_ssl_cleanup(self):
        process = MagicMock()
        process.poll.return_value = None
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, "runtime", Path(directory)), \
                 patch("atlas.vpn.worker_app._authorized"), \
                 patch("atlas.vpn.worker_app.OpenBaoClient"), \
                 patch("atlas.vpn.worker_app.OpenBaoService") as openbao, \
                 patch("atlas.vpn.worker_app._ssl_links", return_value={"lo", "eth0"}), \
                 patch("atlas.vpn.worker_app._ssl_address", return_value=("ppp0", "100.64.0.2")), \
                 patch("atlas.vpn.worker_app.subprocess.Popen", return_value=process), \
                 patch("atlas.vpn.worker_app.socket.create_connection", return_value=connection), \
                 patch("atlas.vpn.worker_app._cleanup_ssl", return_value=True) as cleanup:
                openbao.return_value.read_secret.return_value = ssl_secret()
                result = test_vpn(ssl_request(), "Bearer test")

        cleanup.assert_called_once_with(process, "ppp0")
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["tunnel_established"])
        self.assertTrue(result["target_reachable"])
        self.assertTrue(result["disconnected"])

    def test_early_failure_still_runs_cleanup_and_confirms_disconnect(self):
        with patch("atlas.vpn.worker_app._authorized"), \
             patch("atlas.vpn.worker_app.OpenBaoClient"), \
             patch("atlas.vpn.worker_app.OpenBaoService") as openbao, \
             patch("atlas.vpn.worker_app._profile", side_effect=RuntimeError("failed")), \
             patch("atlas.vpn.worker_app._cleanup", return_value=True) as cleanup:
            openbao.return_value.read_secret.return_value = secret()
            result = test_vpn(request(), "Bearer test")

        cleanup.assert_called_once_with(None)
        self.assertTrue(result["disconnected"])
        self.assertEqual(result["status"], "failed")

    def test_cleanup_failure_is_never_reported_as_disconnected(self):
        with patch("atlas.vpn.worker_app._authorized"), \
             patch("atlas.vpn.worker_app.OpenBaoClient"), \
             patch("atlas.vpn.worker_app.OpenBaoService") as openbao, \
             patch("atlas.vpn.worker_app._profile", side_effect=RuntimeError("failed")), \
             patch("atlas.vpn.worker_app._cleanup", return_value=False):
            openbao.return_value.read_secret.return_value = secret()
            result = test_vpn(request(), "Bearer test")

        self.assertFalse(result["disconnected"])
        self.assertEqual(result["failure_stage"], "cleanup")


if __name__ == "__main__":
    unittest.main()
