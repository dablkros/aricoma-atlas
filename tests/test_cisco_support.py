import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import requests
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.config import Settings
from atlas.integrations.cisco_support import (
    CiscoAccessToken,
    CiscoEoxResult,
    CiscoSupportAPIError,
    CiscoSupportClient,
    CiscoSupportCredentials,
)
from atlas.services.cisco_support import CiscoSupportService
from atlas.services.errors import DependencyError


class Response:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class CiscoSupportClientTests(unittest.TestCase):
    def test_authentication_returns_redacted_typed_token(self):
        requester = Mock()
        requester.request.return_value = Response(
            payload={
                "access_token": "fixture-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )
        client = CiscoSupportClient(requester=requester)
        credentials = CiscoSupportCredentials(
            client_id="fixture-client-id",
            client_secret="fixture-client-secret",
        )

        token = client.authenticate(credentials)

        self.assertEqual(token.expires_in, 3600)
        self.assertNotIn("fixture-access-token", repr(token))
        self.assertNotIn("fixture-client-secret", repr(credentials))

    def test_http_authorization_failures_are_classified(self):
        for status_code, reason in ((401, "authentication_failed"), (403, "not_authorized")):
            with self.subTest(status_code=status_code):
                requester = Mock()
                requester.request.return_value = Response(status_code=status_code)
                client = CiscoSupportClient(requester=requester)

                with self.assertRaises(CiscoSupportAPIError) as raised:
                    client.probe_eox(CiscoAccessToken("fixture-token", 3600))

                self.assertEqual(raised.exception.reason, reason)
                self.assertNotIn("fixture-token", str(raised.exception))

    def test_tls_timeout_and_invalid_json_are_distinct(self):
        cases = (
            (requests.exceptions.SSLError(), "tls_validation_failed"),
            (requests.exceptions.Timeout(), "connection_failed"),
        )
        for error, reason in cases:
            with self.subTest(reason=reason):
                requester = Mock()
                requester.request.side_effect = error
                client = CiscoSupportClient(requester=requester)
                with self.assertRaises(CiscoSupportAPIError) as raised:
                    client.probe_software_suggestion(
                        CiscoAccessToken("fixture-token", 3600)
                    )
                self.assertEqual(raised.exception.reason, reason)

        requester = Mock()
        requester.request.return_value = Response(json_error=ValueError())
        client = CiscoSupportClient(requester=requester)
        with self.assertRaises(CiscoSupportAPIError) as raised:
            client.probe_eox(CiscoAccessToken("fixture-token", 3600))
        self.assertEqual(raised.exception.reason, "invalid_response")

    def test_software_suggestions_are_paginated_and_normalized(self):
        requester = Mock()
        requester.request.side_effect = (
            Response(payload={
                "paginationResponseRecord": {"lastIndex": "2"},
                "suggestions": [{
                    "basePID": "CISCO2811",
                    "softwareType": "IOS Software",
                    "releaseFormat1": "15.1.4M5",
                    "releaseFormat2": "15.1(4)M5",
                    "releaseDate": "17 Nov 2014",
                    "releaseLifeCycle": "MD",
                    "isSuggested": "Y",
                }],
            }),
            Response(payload={
                "paginationResponseRecord": {"lastIndex": "2"},
                "suggestions": [],
            }),
        )
        client = CiscoSupportClient(requester=requester)

        suggestions = client.software_suggestions(
            CiscoAccessToken("fixture-token", 3600),
            "CISCO2811",
            "15.1(4)M5",
        )

        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0].suggested)
        self.assertEqual(suggestions[0].alternate_release, "15.1(4)M5")
        self.assertEqual(
            [call.kwargs["params"]["pageIndex"] for call in requester.request.call_args_list],
            [1, 2],
        )

    def test_eox_serial_response_is_normalized_without_raw_payload(self):
        requester = Mock()
        requester.request.return_value = Response(payload={
            "PaginationResponseRecord": {"LastIndex": 1},
            "EOXRecord": {
                "EOLProductID": "CISCO2811",
                "ProductIDDescription": "Integrated Services Router",
                "EndOfSaleDate": {"value": "2011-11-01"},
                "LastDateOfSupport": {"value": "2016-10-31"},
            },
        })
        client = CiscoSupportClient(requester=requester)

        result = client.eox_by_serial(
            CiscoAccessToken("fixture-token", 3600),
            "FTX1234ABCD",
        )

        self.assertIsInstance(result, CiscoEoxResult)
        self.assertEqual(result.records[0].product_id, "CISCO2811")
        self.assertEqual(result.records[0].last_date_of_support, "2016-10-31")
        self.assertNotIn("fixture-token", repr(result))

    def test_eox_authorization_error_inside_json_is_classified(self):
        requester = Mock()
        requester.request.return_value = Response(payload={
            "EOXError": {"ErrorID": "SSA_ERR_036"},
        })
        client = CiscoSupportClient(requester=requester)

        with self.assertRaises(CiscoSupportAPIError) as raised:
            client.eox_by_serial(
                CiscoAccessToken("fixture-token", 3600),
                "FTX1234ABCD",
            )

        self.assertEqual(raised.exception.reason, "not_authorized")

    def test_software_error_inside_successful_http_response_is_not_ready(self):
        requester = Mock()
        requester.request.return_value = Response(payload={
            "status": "Failed",
            "errorDetailsResponse": {
                "errorCode": "S3_INV_RELEASE",
                "errorDescription": "fixture",
            },
        })
        client = CiscoSupportClient(requester=requester)

        with self.assertRaises(CiscoSupportAPIError) as raised:
            client.probe_software_suggestion(
                CiscoAccessToken("fixture-token", 3600)
            )

        self.assertEqual(raised.exception.reason, "request_failed")

    def test_eox_probe_classifies_error_inside_successful_http_response(self):
        requester = Mock()
        requester.request.return_value = Response(payload={
            "EOXError": {"ErrorID": "SSA_ERR_036"},
        })
        client = CiscoSupportClient(requester=requester)

        with self.assertRaises(CiscoSupportAPIError) as raised:
            client.probe_eox(CiscoAccessToken("fixture-token", 3600))

        self.assertEqual(raised.exception.reason, "not_authorized")


class CiscoSupportServiceTests(unittest.TestCase):
    def test_missing_secret_stops_before_cisco_requests(self):
        openbao = Mock()
        openbao.get_cisco_support_credentials.side_effect = DependencyError(
            "secret_not_found"
        )
        client = Mock()

        result = CiscoSupportService(openbao, client).test_access()

        self.assertEqual(result.overall, "UNAVAILABLE")
        self.assertEqual(result.oauth, "SECRET_NOT_FOUND")
        self.assertEqual(result.software_suggestion, "NOT_TESTED")
        client.authenticate.assert_not_called()

    def test_entitlements_are_reported_independently(self):
        openbao = Mock()
        openbao.get_cisco_support_credentials.return_value = (
            CiscoSupportCredentials("fixture-id", "fixture-secret")
        )
        client = Mock()
        client.authenticate.return_value = CiscoAccessToken("fixture-token", 3600)
        client.probe_eox.side_effect = CiscoSupportAPIError(
            "not_authorized", status_code=403
        )

        result = CiscoSupportService(openbao, client).test_access()

        self.assertEqual(result.overall, "PARTIAL")
        self.assertEqual(result.oauth, "OK")
        self.assertEqual(result.software_suggestion, "OK")
        self.assertEqual(result.eox, "NOT_AUTHORIZED")

    def test_lookup_reuses_token_and_normalized_cache(self):
        openbao = Mock()
        openbao.get_cisco_support_credentials.return_value = (
            CiscoSupportCredentials("fixture-id", "fixture-secret")
        )
        client = Mock()
        client.authenticate.return_value = CiscoAccessToken("fixture-token", 3600)
        client.eox_by_serial.return_value = CiscoEoxResult(())
        service = CiscoSupportService(openbao, client)

        first = service.hardware_eox("FTX1234ABCD")
        second = service.hardware_eox("ftx1234abcd")

        self.assertIs(first, second)
        client.authenticate.assert_called_once()
        client.eox_by_serial.assert_called_once()


class CiscoSupportApiTests(unittest.TestCase):
    def test_endpoint_requires_operator_token_and_returns_safe_states(self):
        with TemporaryDirectory() as temporary_directory:
            token_file = Path(temporary_directory) / "operator-token"
            token = "fixture-operator-token-with-at-least-32-characters"
            token_file.write_text(token, encoding="utf-8")
            service = Mock()
            service.test_access.return_value = SimpleNamespace(
                overall="PARTIAL",
                oauth="OK",
                software_suggestion="OK",
                eox="NOT_AUTHORIZED",
            )
            app = create_app(
                Settings(
                    environment="test",
                    log_level="CRITICAL",
                    monitoring_operator_token_file=token_file,
                    _env_file=None,
                ),
                dependencies=SimpleNamespace(cisco_support=service),
            )
            client = TestClient(app, raise_server_exceptions=False)

            unauthorized = client.post("/api/integrations/cisco/test")
            authorized = client.post(
                "/api/integrations/cisco/test",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(
            authorized.json(),
            {
                "overall": "PARTIAL",
                "oauth": "OK",
                "software_suggestion": "OK",
                "eox": "NOT_AUTHORIZED",
            },
        )
        service.test_access.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
