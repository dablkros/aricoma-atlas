"""OpenBao runtime service using the dedicated Atlas backend AppRole."""

from pathlib import Path
from threading import Lock
from typing import Callable, Optional, Tuple, TypeVar

from atlas.device_credentials import (
    DeviceValidationError,
    credential_path,
    validate_admin_secret,
    validate_snmp_secret,
)
from atlas.openbao_client import (
    OpenBaoAuthenticationError,
    OpenBaoClient,
    OpenBaoConnectionError,
    OpenBaoError,
    OpenBaoIdentityError,
    OpenBaoInvalidResponse,
    OpenBaoNotFound,
)
from atlas.services.errors import DependencyError


T = TypeVar("T")


class OpenBaoService:
    """Cache one short-lived AppRole token in memory and retry auth once."""

    def __init__(self, client: OpenBaoClient, identity_file: Path) -> None:
        self.client = client
        self.identity_file = Path(identity_file)
        self._token: Optional[str] = None
        self._token_lock = Lock()

    @staticmethod
    def _translate_error(exc: OpenBaoError) -> DependencyError:
        if isinstance(exc, OpenBaoConnectionError):
            return DependencyError("connection_failed")
        if isinstance(exc, OpenBaoAuthenticationError) or exc.status_code in {
            401,
            403,
        }:
            return DependencyError("authentication_failed")
        if isinstance(exc, OpenBaoNotFound):
            return DependencyError("secret_not_found")
        if isinstance(exc, OpenBaoInvalidResponse):
            return DependencyError("invalid_response")
        return DependencyError("unavailable")

    def _login_locked(self) -> str:
        try:
            token = self.client.login_from_identity(self.identity_file)
        except OpenBaoIdentityError as exc:
            raise DependencyError("authentication_failed") from exc
        except OpenBaoError as exc:
            raise self._translate_error(exc) from exc

        if not isinstance(token, str) or not token:
            raise DependencyError("authentication_failed")
        self._token = token
        return token

    def _get_token(self) -> Tuple[str, bool]:
        with self._token_lock:
            if self._token:
                return self._token, True
            return self._login_locked(), False

    def _refresh_token(self, stale_token: str) -> str:
        with self._token_lock:
            if self._token and self._token != stale_token:
                return self._token
            self._token = None
            return self._login_locked()

    def _authenticated(self, operation: Callable[[str], T]) -> T:
        token, was_cached = self._get_token()
        try:
            return operation(token)
        except OpenBaoAuthenticationError as exc:
            if not was_cached:
                raise DependencyError("authentication_failed") from exc
        except OpenBaoError as exc:
            raise self._translate_error(exc) from exc

        refreshed = self._refresh_token(token)
        try:
            return operation(refreshed)
        except OpenBaoError as exc:
            raise self._translate_error(exc) from exc

    def check_ready(self) -> None:
        try:
            status = self.client.seal_status()
        except OpenBaoError as exc:
            raise self._translate_error(exc) from exc

        if not isinstance(status, dict):
            raise DependencyError("invalid_response")
        if status.get("initialized") is not True:
            raise DependencyError("not_initialized")
        if status.get("sealed") is not False:
            raise DependencyError("sealed")

        self._authenticated(self.client.lookup_self)

    def read_secret(self, path: str) -> dict:
        return self._authenticated(
            lambda token: self.client.kv_read(token, path, mount="atlas")
        )

    def read_secret_with_metadata(self, path: str) -> tuple[dict, dict]:
        return self._authenticated(
            lambda token: self.client.kv_read_with_metadata(
                token,
                path,
                mount="atlas",
            )
        )

    def get_netbox_api_token(self) -> str:
        secret = self.read_secret("netbox/api")
        token = secret.get("token") if isinstance(secret, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise DependencyError("secret_not_found")
        return token

    def get_zabbix_api_token(self) -> str:
        secret = self.read_secret("zabbix/api")
        token = secret.get("token") if isinstance(secret, dict) else None
        if (
            not isinstance(token, str)
            or not token
            or token != token.strip()
            or any(character.isspace() for character in token)
        ):
            raise DependencyError("secret_not_found")
        return token

    def get_snmp_credentials(self, manufacturer: str, profile: str) -> tuple[dict, int]:
        try:
            path = credential_path(manufacturer, profile, purpose="snmp")
            secret, metadata = self.read_secret_with_metadata(path)
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                raise DeviceValidationError(
                    "SNMP credential profile not found",
                    code="snmp_credential_not_found",
                    category="error",
                ) from None
            raise
        try:
            validated = validate_snmp_secret(secret)
        except DeviceValidationError as exc:
            raise DeviceValidationError(
                "Invalid SNMP credential schema",
                code="invalid_snmp_credential_schema",
                category="error",
            ) from exc
        version = metadata.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DependencyError("invalid_response")
        return validated, version

    def get_device_credentials(
        self,
        manufacturer: str,
        profile: str,
        *,
        authentication: str = "ssh",
    ) -> dict:
        try:
            path = credential_path(manufacturer, profile, purpose="admin")
            secret = self.read_secret(path)
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                raise DeviceValidationError(
                    "Credential profile not found",
                    code="credential_not_found",
                    category="error",
                ) from None
            raise
        try:
            return validate_admin_secret(secret, authentication=authentication)
        except DeviceValidationError as exc:
            raise DeviceValidationError(
                str(exc),
                code=exc.code,
                category="error",
            ) from exc


__all__ = [
    "OpenBaoAuthenticationError",
    "OpenBaoClient",
    "OpenBaoConnectionError",
    "OpenBaoError",
    "OpenBaoIdentityError",
    "OpenBaoInvalidResponse",
    "OpenBaoNotFound",
    "OpenBaoService",
]
