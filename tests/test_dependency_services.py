import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

import requests

from atlas.openbao_client import (
    OpenBaoAuthenticationError,
    OpenBaoClient,
    OpenBaoError,
    OpenBaoNotFound,
)
from atlas.device_credentials import DeviceValidationError
from atlas.services.errors import DependencyError
from atlas.services.netbox import NetBoxService
from atlas.services.openbao import OpenBaoService
from atlas.services.oxidized import OxidizedService


class FakeResponse:
    def __init__(self, status_code=200, data=None, json_error=None):
        self.status_code = status_code
        self.data = data
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.data


class QueueRequester:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OpenBaoServiceTests(unittest.TestCase):
    def service(self, client=None):
        return OpenBaoService(client or Mock(), Path("backend-identity.json"))

    def test_successful_approle_login_and_readiness(self):
        client = Mock()
        client.seal_status.return_value = {"initialized": True, "sealed": False}
        client.login_from_identity.return_value = "runtime-token"
        client.lookup_self.return_value = {"policies": ["atlas-backend"]}
        service = self.service(client)

        service.check_ready()

        client.login_from_identity.assert_called_once_with(
            Path("backend-identity.json")
        )
        client.lookup_self.assert_called_once_with("runtime-token")

    def test_missing_and_invalid_identity_files_are_auth_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "invalid.json"
            invalid.write_text(json.dumps(["not-an-object"]), encoding="utf-8")

            for identity in (root / "missing.json", invalid):
                with self.subTest(identity=identity):
                    service = OpenBaoService(OpenBaoClient(), identity)
                    with self.assertRaises(DependencyError) as raised:
                        service.read_secret("netbox/api")
                    self.assertEqual(raised.exception.reason, "authentication_failed")

    def test_sealed_openbao_fails_without_login(self):
        client = Mock()
        client.seal_status.return_value = {"initialized": True, "sealed": True}

        with self.assertRaises(DependencyError) as raised:
            self.service(client).check_ready()

        self.assertEqual(raised.exception.reason, "sealed")
        client.login_from_identity.assert_not_called()

    def test_uninitialized_openbao_fails_without_login(self):
        client = Mock()
        client.seal_status.return_value = {"initialized": False, "sealed": True}

        with self.assertRaises(DependencyError) as raised:
            self.service(client).check_ready()

        self.assertEqual(raised.exception.reason, "not_initialized")
        client.login_from_identity.assert_not_called()

    def test_auth_failure_is_redacted(self):
        client = Mock()
        client.login_from_identity.side_effect = OpenBaoAuthenticationError(
            "secret_id=sensitive",
            status_code=403,
        )

        with self.assertRaises(DependencyError) as raised:
            self.service(client).read_secret("netbox/api")

        self.assertEqual(raised.exception.reason, "authentication_failed")
        self.assertNotIn("sensitive", str(raised.exception))

    def test_login_server_failure_is_not_mislabeled_as_bad_credentials(self):
        client = Mock()
        client.login_from_identity.side_effect = OpenBaoError(
            "internal response",
            status_code=500,
        )

        with self.assertRaises(DependencyError) as raised:
            self.service(client).read_secret("netbox/api")

        self.assertEqual(raised.exception.reason, "unavailable")

    def test_cached_token_is_refreshed_once_after_unauthorized(self):
        client = Mock()
        client.login_from_identity.side_effect = ["token-one", "token-two"]
        client.kv_read.side_effect = [
            {"token": "nbt_first"},
            OpenBaoAuthenticationError("expired", status_code=403),
            {"token": "nbt_second"},
        ]
        service = self.service(client)

        self.assertEqual(service.get_netbox_api_token(), "nbt_first")
        self.assertEqual(service.get_netbox_api_token(), "nbt_second")

        self.assertEqual(client.login_from_identity.call_count, 2)
        self.assertEqual(
            client.kv_read.call_args_list,
            [
                call("token-one", "netbox/api", mount="atlas"),
                call("token-one", "netbox/api", mount="atlas"),
                call("token-two", "netbox/api", mount="atlas"),
            ],
        )

    def test_new_token_auth_failure_is_not_retried_forever(self):
        client = Mock()
        client.login_from_identity.return_value = "token-one"
        client.kv_read.side_effect = OpenBaoAuthenticationError(
            "denied",
            status_code=403,
        )

        with self.assertRaises(DependencyError) as raised:
            self.service(client).get_netbox_api_token()

        self.assertEqual(raised.exception.reason, "authentication_failed")
        self.assertEqual(client.login_from_identity.call_count, 1)
        self.assertEqual(client.kv_read.call_count, 1)

    def test_missing_secret_is_distinct(self):
        client = Mock()
        client.login_from_identity.return_value = "runtime-token"
        client.kv_read.side_effect = OpenBaoNotFound("not found")

        with self.assertRaises(DependencyError) as raised:
            self.service(client).get_netbox_api_token()

        self.assertEqual(raised.exception.reason, "secret_not_found")

    def test_device_secret_missing_and_malformed_are_isolated_and_redacted(self):
        cases = (
            (
                OpenBaoNotFound("password=sensitive"),
                "credential_not_found",
            ),
            (
                {"username": "fixture-user", "password": ""},
                "invalid_credentials",
            ),
        )
        for value, code in cases:
            with self.subTest(code=code):
                client = Mock()
                client.login_from_identity.return_value = "runtime-token"
                if isinstance(value, Exception):
                    client.kv_read.side_effect = value
                else:
                    client.kv_read.return_value = value

                with self.assertRaises(DeviceValidationError) as raised:
                    self.service(client).get_device_credentials("cisco", "default")

                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.category, "error")
                self.assertNotIn("sensitive", str(raised.exception))


class NetBoxServiceTests(unittest.TestCase):
    def service(self, requester, token="nbt_key.plaintext"):
        openbao = Mock()
        openbao.get_netbox_api_token.return_value = token
        return (
            NetBoxService(
                "https://netbox.example.test",
                openbao,
                2.0,
                8.0,
                requester=requester,
            ),
            openbao,
        )

    def test_v2_token_uses_bearer_and_successful_readiness(self):
        requester = QueueRequester(
            FakeResponse(data={"count": 0, "next": None, "results": []})
        )
        service, _openbao = self.service(requester)

        service.check_ready()

        url, kwargs = requester.calls[0]
        self.assertEqual(url, "https://netbox.example.test/api/dcim/devices/")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer nbt_key.plaintext")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["timeout"], (2.0, 8.0))

    def test_legacy_token_uses_token_scheme(self):
        requester = QueueRequester(
            FakeResponse(data={"count": 0, "next": None, "results": []})
        )
        service, _openbao = self.service(requester, token="legacy-token")

        service.check_ready()

        self.assertEqual(
            requester.calls[0][1]["headers"]["Authorization"],
            "Token legacy-token",
        )

    def test_unauthorized_unreachable_and_malformed_json_are_distinct(self):
        cases = (
            (FakeResponse(status_code=403), "authentication_failed"),
            (requests.ConnectionError("token=sensitive"), "connection_failed"),
            (FakeResponse(data=None, json_error=ValueError("bad")), "invalid_response"),
        )
        for response, reason in cases:
            with self.subTest(reason=reason):
                service, _openbao = self.service(QueueRequester(response))
                with self.assertRaises(DependencyError) as raised:
                    service.check_ready()
                self.assertEqual(raised.exception.reason, reason)
                self.assertNotIn("sensitive", str(raised.exception))

    def test_pagination_follows_same_origin_next_links(self):
        requester = QueueRequester(
            FakeResponse(
                data={
                    "next": "https://netbox.example.test/api/dcim/devices/?limit=1&offset=1",
                    "results": [{"id": 1}],
                }
            ),
            FakeResponse(data={"next": None, "results": [{"id": 2}]}),
        )
        service, _openbao = self.service(requester)

        result = service.get_all("/api/dcim/devices/", params={"limit": 1})

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(requester.calls[1][1]["params"], None)

    def test_pagination_rejects_cross_origin_next_link(self):
        requester = QueueRequester(
            FakeResponse(
                data={
                    "next": "https://attacker.example/steal",
                    "results": [{"id": 1}],
                }
            )
        )
        service, _openbao = self.service(requester)

        with self.assertRaises(DependencyError) as raised:
            service.get_all("/api/dcim/devices/")

        self.assertEqual(raised.exception.reason, "invalid_response")
        self.assertEqual(len(requester.calls), 1)

    def test_pagination_rejects_credentials_in_next_link(self):
        requester = QueueRequester(
            FakeResponse(
                data={
                    "next": "https://user:pass@netbox.example.test/api/dcim/devices/",
                    "results": [],
                }
            )
        )
        service, _openbao = self.service(requester)

        with self.assertRaises(DependencyError) as raised:
            service.get_all("/api/dcim/devices/")

        self.assertEqual(raised.exception.reason, "invalid_response")

    def test_oxidized_selection_uses_active_enabled_filter_and_pagination(self):
        requester = QueueRequester(
            FakeResponse(
                data={
                    "next": "https://netbox.example.test/api/dcim/devices/?offset=1",
                    "results": [{"id": 1}],
                }
            ),
            FakeResponse(data={"next": None, "results": [{"id": 2}]}),
        )
        service, _openbao = self.service(requester)

        result = service.get_oxidized_devices()

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(
            requester.calls[0][1]["params"],
            {
                "cf_oxidized_enabled": "true",
                "status": "active",
            },
        )


class OxidizedServiceTests(unittest.TestCase):
    def service(self, requester, sleeper=None):
        return OxidizedService(
            "http://atlas-oxidized:8888",
            2.0,
            8.0,
            requester=requester,
            sleeper=sleeper,
        )

    def test_empty_inventory_is_ready(self):
        requester = QueueRequester(FakeResponse(data=[]))

        self.service(requester).check_ready()

        self.assertEqual(
            requester.calls[0][0],
            "http://atlas-oxidized:8888/nodes.json",
        )

    def test_nonempty_inventory_is_ready(self):
        requester = QueueRequester(FakeResponse(data=[{"name": "router-1"}]))

        self.service(requester).check_ready()

        self.assertEqual(requester.calls[0][1]["timeout"], (2.0, 8.0))

    def test_unreachable_is_connection_failure(self):
        requester = QueueRequester(requests.ConnectionError("unreachable"))

        with self.assertRaises(DependencyError) as raised:
            self.service(requester).check_ready()

        self.assertEqual(raised.exception.reason, "connection_failed")

    def test_reload_success_and_empty_waiting_state(self):
        success = QueueRequester(FakeResponse(data=["reloaded list of nodes"]))
        self.service(success).reload_inventory([{"name": "SW01"}])
        self.assertEqual(
            success.calls[0][0],
            "http://atlas-oxidized:8888/reload?format=json",
        )

        waiting = QueueRequester(FakeResponse(status_code=503))
        self.service(waiting).reload_inventory([])

    def test_first_inventory_waits_for_native_oxidized_transition(self):
        requester = QueueRequester(
            FakeResponse(
                status_code=503,
                data={"status": "waiting_for_inventory"},
            ),
            requests.ConnectionError("native process is starting"),
            FakeResponse(data=["reloaded list of nodes"]),
        )

        self.service(requester, sleeper=lambda _delay: None).reload_inventory(
            [{"name": "R1"}]
        )

        self.assertEqual(len(requester.calls), 3)

    def test_reload_timeout_http_error_and_malformed_response(self):
        cases = (
            (requests.Timeout("timeout"), "connection_failed"),
            (FakeResponse(status_code=500), "unavailable"),
            (FakeResponse(data={"unexpected": True}), "invalid_response"),
            (
                FakeResponse(data=None, json_error=ValueError("bad")),
                "invalid_response",
            ),
        )
        for response, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(DependencyError) as raised:
                    self.service(QueueRequester(response)).reload_inventory(
                        [{"name": "SW01"}]
                    )
                self.assertEqual(raised.exception.reason, reason)

    def test_queue_backup_url_encodes_device_name(self):
        requester = QueueRequester(FakeResponse(status_code=202))

        self.service(requester).queue_backup("SW:01")

        self.assertEqual(
            requester.calls[0][0],
            "http://atlas-oxidized:8888/node/next/SW%3A01",
        )
        self.assertEqual(requester.calls[0][1]["timeout"], (2.0, 8.0))

    def test_queue_backup_maps_transport_auth_and_http_failures(self):
        cases = (
            (requests.Timeout("timeout"), "connection_failed"),
            (FakeResponse(status_code=401), "authentication_failed"),
            (FakeResponse(status_code=500), "unavailable"),
        )
        for response, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(DependencyError) as raised:
                    self.service(QueueRequester(response)).queue_backup("SW01")
                self.assertEqual(raised.exception.reason, reason)


if __name__ == "__main__":
    unittest.main()
