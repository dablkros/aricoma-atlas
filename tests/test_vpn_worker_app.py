import base64
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from atlas.vpn.worker_app import (
    FortinetFCTError, WorkerSessionOpenRequest, WorkerTestRequest,
    _fortinet_fct_payload, _openvpn_target_ips, _profile, _render_ssl_config,
    _ssl_profile, _start_charon, _write_private_bytes, settings, test_vpn,
)


def request():
    return WorkerTestRequest(
        connection_id="customer-vpn", tenant_id=1,
        secret_ref="vpn/connections/customer-vpn",
        gateway_host="vpn.example.test", gateway_port=500,
        driver="strongswan", protocol="ipsec-ikev2",
        target_ip="10.0.0.10", target_port=22,
    )


def http_request():
    return Request({
        "type": "http",
        "headers": [(b"x-request-id", b"vpn-test-request")],
    })


def secret():
    fct_payload = b'approved-lab-fct-payload\x00'
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
        "fct_payload_base64": base64.b64encode(fct_payload).decode("ascii"),
        "fct_payload_sha256": hashlib.sha256(fct_payload).hexdigest(),
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
    def test_charon_readiness_removes_stale_socket_and_probes_vici(self):
        process = MagicMock()
        process.poll.return_value = None
        probe = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "charon.vici"
            socket_path.write_text("stale", encoding="utf-8")
            with patch("atlas.vpn.worker_app.subprocess.Popen", return_value=process), \
                 patch("atlas.vpn.worker_app._run", return_value=probe) as run:
                started = _start_charon(socket_path=socket_path)

            self.assertFalse(socket_path.exists())
        self.assertIs(started, process)
        run.assert_called_once_with([settings.swanctl, "--stats"], timeout=2, check=False)

    def test_explicit_mfa_modes_and_legacy_boolean_are_distinguished(self):
        self.assertEqual(
            request().model_copy(update={"mfa_mode": "push"}).effective_mfa_mode,
            "push",
        )
        self.assertEqual(
            request().model_copy(update={"mfa_mode": "otp"}).effective_mfa_mode,
            "otp",
        )
        self.assertEqual(
            request().model_copy(update={"external_mfa": True}).effective_mfa_mode,
            "auto",
        )

    def test_connectivity_test_routes_proof_target_and_session_routes_devices(self):
        self.assertEqual(_openvpn_target_ips(request()), ("10.0.0.10",))
        session = WorkerSessionOpenRequest(
            connection_id="customer-vpn",
            tenant_id=1,
            secret_ref="vpn/connections/customer-vpn",
            gateway_host="vpn.example.test",
            gateway_port=1194,
            driver="openvpn",
            protocol="openvpn",
            target_ips=["10.1.248.20", "10.1.248.21"],
        )
        self.assertEqual(
            _openvpn_target_ips(session),
            ("10.1.248.20", "10.1.248.21"),
        )

    def test_session_request_accepts_only_explicit_ipv4_targets(self):
        body = WorkerSessionOpenRequest(
            connection_id="customer-vpn",
            tenant_id=1,
            secret_ref="vpn/connections/customer-vpn",
            gateway_host="vpn.example.test",
            gateway_port=1194,
            driver="openvpn",
            protocol="openvpn",
            target_ips=["10.1.248.20", "10.1.248.20"],
        )
        self.assertEqual(body.target_ips, ["10.1.248.20"])

        with self.assertRaises(ValueError):
            WorkerSessionOpenRequest(
                connection_id="customer-vpn",
                tenant_id=1,
                secret_ref="vpn/connections/customer-vpn",
                gateway_host="vpn.example.test",
                gateway_port=1194,
                driver="openvpn",
                protocol="openvpn",
                target_ips=["customer.internal"],
            )

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

    def test_fct_payload_requires_content_and_matching_hash(self):
        value = secret()
        expected = base64.b64decode(value["fct_payload_base64"])
        self.assertEqual(_fortinet_fct_payload(value), expected)

        for broken in (
            {key: item for key, item in value.items() if key != "fct_payload_base64"},
            {**value, "fct_payload_base64": "not-base64"},
            {**value, "fct_payload_sha256": "0" * 64},
        ):
            with self.subTest(broken=broken), self.assertRaises(FortinetFCTError):
                _fortinet_fct_payload(broken)

    def test_fct_payload_is_written_as_private_exclusive_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fct-data.bin"
            _write_private_bytes(path, b"opaque-fct")
            self.assertEqual(path.read_bytes(), b"opaque-fct")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FortinetFCTError):
                _write_private_bytes(path, b"replacement")

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
                result = test_vpn(ssl_request(), http_request(), "Bearer test")

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
            result = test_vpn(request(), http_request(), "Bearer test")

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
            result = test_vpn(request(), http_request(), "Bearer test")

        self.assertFalse(result["disconnected"])
        self.assertEqual(result["failure_stage"], "cleanup")


if __name__ == "__main__":
    unittest.main()
