#!/usr/bin/env python3

import base64
import json
import re
from pathlib import Path

import requests


class OpenBaoError(RuntimeError):
    """OpenBao failure with an optional machine-readable HTTP status."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class OpenBaoConnectionError(OpenBaoError):
    pass


class OpenBaoAuthenticationError(OpenBaoError):
    pass


class OpenBaoInvalidResponse(OpenBaoError):
    pass


class OpenBaoIdentityError(OpenBaoError):
    pass


class OpenBaoNotFound(OpenBaoError):
    def __init__(self, message, *, status_code=404):
        super().__init__(message, status_code=status_code)


class OpenBaoClient:
    def __init__(
        self,
        base_url="http://127.0.0.1:18200",
        timeout=15,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # =======================================================================
    # HTTP
    # =======================================================================

    def _request(
        self,
        method,
        path,
        token=None,
        payload=None,
        expected=(200, 204),
    ):
        headers = {
            "Accept": "application/json",
        }

        if token:
            headers["X-Vault-Token"] = token

        try:
            response = requests.request(
                method,
                (
                    f"{self.base_url}"
                    f"/v1/{path.lstrip('/')}"
                ),
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )

        except requests.RequestException:
            raise OpenBaoConnectionError("Unable to contact OpenBao") from None

        if response.status_code not in expected:
            if response.status_code == 404:
                error_type = OpenBaoNotFound
            elif response.status_code in {401, 403}:
                error_type = OpenBaoAuthenticationError
            else:
                error_type = OpenBaoError
            raise error_type(
                f"OpenBao request failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )

        return response

    @staticmethod
    def _json_object(response):
        try:
            data = response.json()
        except ValueError:
            raise OpenBaoInvalidResponse(
                "OpenBao returned an invalid JSON response"
            ) from None
        if not isinstance(data, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned an invalid response object"
            )
        return data

    # =======================================================================
    # Status
    # =======================================================================

    def seal_status(self):
        response = self._request(
            "GET",
            "sys/seal-status",
        )

        return self._json_object(response)

    # =======================================================================
    # Identity
    # =======================================================================

    @staticmethod
    def load_identity(path):
        path = Path(path)

        if not path.exists():
            raise OpenBaoIdentityError(
                f"OpenBao identity file not found: "
                f"{path}"
            )

        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(handle)

        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise OpenBaoIdentityError(
                f"Unable to read OpenBao identity: "
                f"{path}"
            ) from exc

        if not isinstance(data, dict):
            raise OpenBaoIdentityError(
                f"Invalid OpenBao identity file: {path}"
            )

        role_id = data.get("role_id")
        secret_id = data.get("secret_id")

        if (
            not isinstance(role_id, str)
            or not role_id.strip()
            or not isinstance(secret_id, str)
            or not secret_id.strip()
        ):
            raise OpenBaoIdentityError(
                f"Invalid OpenBao identity file: "
                f"{path}"
            )

        return {
            "role_id": role_id,
            "secret_id": secret_id,
        }

    def login_approle(
        self,
        role_id,
        secret_id,
    ):
        response = self._request(
            "POST",
            "auth/approle/login",
            payload={
                "role_id": role_id,
                "secret_id": secret_id,
            },
        )

        auth = self._json_object(response).get("auth", {})
        if not isinstance(auth, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned invalid authentication data"
            )
        token = auth.get("client_token")

        if not token:
            raise OpenBaoInvalidResponse(
                "AppRole login did not "
                "return a token"
            )

        return token

    def login_from_identity(self, path):
        identity = self.load_identity(
            path
        )

        return self.login_approle(
            identity["role_id"],
            identity["secret_id"],
        )

    # =======================================================================
    # Token operations
    # =======================================================================

    def lookup_self(self, token):
        response = self._request(
            "GET",
            "auth/token/lookup-self",
            token=token,
        )

        data = self._json_object(response).get(
            "data",
            {},
        )

        if not isinstance(data, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned invalid token metadata"
            )

        return data

    def revoke_self(self, token):
        self._request(
            "POST",
            "auth/token/revoke-self",
            token=token,
            expected=(
                200,
                204,
            ),
        )

    # =======================================================================
    # Root generation
    # =======================================================================
    def root_generation_status(
            self,
            operator_token,
    ):
        response = self._request(
            "GET",
            "sys/generate-root-token/attempt",
            token=operator_token,
        )

        body = self._json_object(response)

        return body.get(
            "data",
            body,
        )

    def start_root_generation(
            self,
            operator_token,
    ):
        response = self._request(
            "POST",
            "sys/generate-root-token/attempt",
            token=operator_token,
            payload={},
        )

        body = self._json_object(response)

        data = body.get(
            "data",
            body,
        )

        if not data.get("started"):
            raise OpenBaoError(
                "Root token generation did not start"
            )

        if not data.get("nonce"):
            raise OpenBaoError(
                "Root token generation did not return a nonce"
            )

        if not data.get("otp"):
            raise OpenBaoError(
                "Root token generation did not return an OTP"
            )

        return data

    def cancel_root_generation(
        self,
        operator_token,
    ):
        self._request(
            "DELETE",
            (
                "sys/generate-root-token/"
                "attempt"
            ),
            token=operator_token,
            expected=(
                200,
                204,
            ),
        )

    def submit_root_share(
            self,
            operator_token,
            nonce,
            key,
    ):
        response = self._request(
            "POST",
            "sys/generate-root-token/update",
            token=operator_token,
            payload={
                "key": key,
                "nonce": nonce,
            },
        )

        body = self._json_object(response)

        return body.get(
            "data",
            body,
        )

    # =======================================================================
    # OTP decoding
    # =======================================================================

    @staticmethod
    def decode_root_token(
            encoded_token,
            otp,
    ):
        if not encoded_token:
            raise OpenBaoError(
                "Encoded root token is empty"
            )

        if not otp:
            raise OpenBaoError(
                "Root token OTP is empty"
            )

        try:
            padding = (
                    "=" * (-len(encoded_token) % 4)
            )

            encoded_bytes = base64.b64decode(
                encoded_token + padding
            )

        except Exception as exc:
            raise OpenBaoError(
                "Unable to Base64-decode "
                "the root token"
            ) from exc

        otp_bytes = otp.encode(
            "utf-8"
        )

        if len(encoded_bytes) != len(otp_bytes):
            raise OpenBaoError(
                "OTP length does not match "
                "encoded root token length"
            )

        decoded_bytes = bytes(
            value ^ otp_bytes[index]
            for index, value
            in enumerate(encoded_bytes)
        )

        try:
            return decoded_bytes.decode(
                "utf-8"
            )

        except UnicodeDecodeError as exc:
            raise OpenBaoError(
                "Unable to decode generated "
                "root token"
            ) from exc

    # =======================================================================
    # Temporary root
    # =======================================================================

    @staticmethod
    def validate_ttl(ttl):
        if not isinstance(ttl, str):
            raise OpenBaoError(
                "TTL must be a string"
            )

        if not re.fullmatch(
            r"[1-9][0-9]*[smh]",
            ttl,
        ):
            raise OpenBaoError(
                "TTL must use the format "
                "15m, 1h, 4h, etc."
            )

    def create_temporary_root_token(
        self,
        generated_root_token,
        ttl,
    ):
        self.validate_ttl(
            ttl
        )

        response = self._request(
            "POST",
            "auth/token/create-orphan",
            token=generated_root_token,
            payload={
                "policies": [
                    "root",
                ],
                "ttl": ttl,
                "explicit_max_ttl": ttl,
                "renewable": False,
                "no_default_policy": True,
                "display_name":
                    "atlas-temporary-root",
            },
        )

        auth = self._json_object(response).get(
            "auth",
            {},
        )

        if not isinstance(auth, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned invalid authentication data"
            )

        token = auth.get(
            "client_token"
        )

        if not token:
            raise OpenBaoError(
                "Temporary root token "
                "was not returned"
            )

        return {
            "token": token,
            "lease_duration":
                auth.get(
                    "lease_duration"
                ),
            "renewable":
                auth.get(
                    "renewable"
                ),
            "policies":
                auth.get(
                    "policies",
                    [],
                ),
        }

    def exchange_generated_root(
        self,
        generated_root_token,
        ttl,
    ):
        """
        Exchange the quorum-generated,
        non-expiring root token for an
        orphan root token with a hard TTL.

        The generated bootstrap root token
        is revoked immediately afterwards.
        """

        temporary = None

        try:
            temporary = (
                self.create_temporary_root_token(
                    generated_root_token,
                    ttl,
                )
            )

        except Exception:
            try:
                self.revoke_self(
                    generated_root_token
                )
            finally:
                raise

        try:
            self.revoke_self(
                generated_root_token
            )

        except Exception as exc:
            # Do not leave two usable root
            # tokens behind if revocation of
            # the bootstrap root fails.
            try:
                self.revoke_self(
                    temporary["token"]
                )
            except Exception:
                pass

            raise OpenBaoError(
                "Temporary root was created, "
                "but bootstrap root revocation "
                "failed. Temporary root was "
                "also revoked as a precaution."
            ) from exc

        return temporary


    def kv_read(
        self,
        token,
        path,
        mount="atlas",
    ):
        data, _metadata = self.kv_read_with_metadata(
            token,
            path,
            mount=mount,
        )
        return data

    def kv_read_with_metadata(
        self,
        token,
        path,
        mount="atlas",
    ):
        response = self._request(
            "GET",
            f"{mount}/data/{path.lstrip('/')}",
            token=token,
        )

        envelope = self._json_object(response).get("data", {})
        if not isinstance(envelope, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned an invalid KV response"
            )
        data = envelope.get("data", {})
        metadata = envelope.get("metadata", {})
        if not isinstance(data, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned invalid KV data"
            )
        if not isinstance(metadata, dict):
            raise OpenBaoInvalidResponse(
                "OpenBao returned invalid KV metadata"
            )
        return data, metadata


    def kv_write(
        self,
        token,
        path,
        data,
        mount="atlas",
    ):
        self._request(
            "POST",
            f"{mount}/data/{path.lstrip('/')}",
            token=token,
            payload={
                "data": data,
            },
        )
