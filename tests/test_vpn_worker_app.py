import unittest
from unittest.mock import patch

from atlas.vpn.worker_app import WorkerTestRequest, _profile, test_vpn


def request():
    return WorkerTestRequest(
        connection_id="customer-vpn", tenant_id=1,
        secret_ref="vpn/connections/customer-vpn",
        gateway_host="vpn.example.test", gateway_port=500,
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
