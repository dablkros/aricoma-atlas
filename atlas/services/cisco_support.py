"""Cisco Support API diagnostics using OpenBao-backed credentials."""

from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable, Literal, TypeVar, cast

from atlas.integrations.cisco_support import (
    CiscoAccessToken,
    CiscoEoxResult,
    CiscoSoftwareSuggestion,
    CiscoSupportAPIError,
    CiscoSupportClient,
)
from atlas.services.errors import DependencyError
from atlas.services.openbao import OpenBaoService


CiscoDiagnosticState = Literal[
    "OK",
    "NOT_TESTED",
    "SECRET_NOT_FOUND",
    "INVALID_SECRET",
    "CONNECTION_FAILED",
    "TLS_VALIDATION_FAILED",
    "AUTHENTICATION_FAILED",
    "NOT_AUTHORIZED",
    "INVALID_RESPONSE",
    "UNAVAILABLE",
]
T = TypeVar("T")


@dataclass(frozen=True)
class CiscoSupportTestResult:
    overall: Literal["READY", "PARTIAL", "UNAVAILABLE"]
    oauth: CiscoDiagnosticState
    software_suggestion: CiscoDiagnosticState
    eox: CiscoDiagnosticState


class CiscoSupportService:
    def __init__(
        self,
        openbao: OpenBaoService,
        client: CiscoSupportClient,
        *,
        cache_ttl: float = 3600,
    ) -> None:
        self.openbao = openbao
        self.client = client
        self.cache_ttl = cache_ttl
        self._lock = RLock()
        self._token: CiscoAccessToken | None = None
        self._token_expires_at = 0.0
        self._cache: dict[tuple[str, ...], tuple[float, object]] = {}

    def _authenticate(self) -> CiscoAccessToken:
        now = monotonic()
        with self._lock:
            if self._token is not None and now < self._token_expires_at:
                return self._token
            credentials = self.openbao.get_cisco_support_credentials()
            token = self.client.authenticate(credentials)
            self._token = token
            self._token_expires_at = now + max(token.expires_in - 60, 1)
            return token

    def _cached(
        self,
        key: tuple[str, ...],
        fetch: Callable[[CiscoAccessToken], T],
    ) -> T:
        now = monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now < cached[0]:
                return cast(T, cached[1])
            try:
                value = fetch(self._authenticate())
            except CiscoSupportAPIError as exc:
                if exc.reason != "authentication_failed":
                    raise
                self._token = None
                self._token_expires_at = 0.0
                value = fetch(self._authenticate())
            self._cache[key] = (now + self.cache_ttl, value)
            return value

    def software_suggestions(
        self,
        product_id: str,
        installed_release: str,
    ) -> tuple[CiscoSoftwareSuggestion, ...]:
        return self._cached(
            ("software", product_id.casefold(), installed_release.casefold()),
            lambda token: self.client.software_suggestions(
                token,
                product_id,
                installed_release,
            ),
        )

    def hardware_eox(self, serial_number: str) -> CiscoEoxResult:
        return self._cached(
            ("hardware_eox", serial_number.casefold()),
            lambda token: self.client.eox_by_serial(token, serial_number),
        )

    def software_eox(
        self,
        installed_release: str,
        os_type: str | None,
    ) -> CiscoEoxResult:
        return self._cached(
            (
                "software_eox",
                installed_release.casefold(),
                os_type.casefold() if os_type is not None else "",
            ),
            lambda token: self.client.eox_by_software(
                token,
                installed_release,
                os_type,
            ),
        )

    @staticmethod
    def _dependency_state(exc: DependencyError) -> CiscoDiagnosticState:
        return {
            "secret_not_found": "SECRET_NOT_FOUND",
            "invalid_credential_schema": "INVALID_SECRET",
            "connection_failed": "CONNECTION_FAILED",
            "authentication_failed": "AUTHENTICATION_FAILED",
            "invalid_response": "INVALID_RESPONSE",
        }.get(exc.reason, "UNAVAILABLE")

    @staticmethod
    def _api_state(exc: CiscoSupportAPIError) -> CiscoDiagnosticState:
        return {
            "connection_failed": "CONNECTION_FAILED",
            "tls_validation_failed": "TLS_VALIDATION_FAILED",
            "authentication_failed": "AUTHENTICATION_FAILED",
            "not_authorized": "NOT_AUTHORIZED",
            "invalid_response": "INVALID_RESPONSE",
        }.get(exc.reason, "UNAVAILABLE")

    def test_access(self) -> CiscoSupportTestResult:
        try:
            credentials = self.openbao.get_cisco_support_credentials()
        except DependencyError as exc:
            state = self._dependency_state(exc)
            return CiscoSupportTestResult(
                overall="UNAVAILABLE",
                oauth=state,
                software_suggestion="NOT_TESTED",
                eox="NOT_TESTED",
            )

        try:
            token = self.client.authenticate(credentials)
        except CiscoSupportAPIError as exc:
            state = self._api_state(exc)
            return CiscoSupportTestResult(
                overall="UNAVAILABLE",
                oauth=state,
                software_suggestion="NOT_TESTED",
                eox="NOT_TESTED",
            )
        with self._lock:
            self._token = token
            self._token_expires_at = monotonic() + max(token.expires_in - 60, 1)

        states: dict[str, CiscoDiagnosticState] = {}
        for name, probe in (
            ("software_suggestion", self.client.probe_software_suggestion),
            ("eox", self.client.probe_eox),
        ):
            try:
                probe(token)
            except CiscoSupportAPIError as exc:
                states[name] = self._api_state(exc)
            else:
                states[name] = "OK"

        overall = (
            "READY"
            if all(value == "OK" for value in states.values())
            else "PARTIAL"
        )
        return CiscoSupportTestResult(
            overall=overall,
            oauth="OK",
            software_suggestion=states["software_suggestion"],
            eox=states["eox"],
        )


__all__ = [
    "CiscoDiagnosticState",
    "CiscoSupportService",
    "CiscoSupportTestResult",
]
