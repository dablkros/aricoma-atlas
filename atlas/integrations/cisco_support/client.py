"""Secret-safe HTTP boundary for Cisco Support APIs."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests


TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
EOX_PROBE_URL = (
    "https://apix.cisco.com/supporttools/eox/rest/5/"
    "EOXByProductID/1/CISCO2811?responseencoding=json"
)
SOFTWARE_COMPATIBLE_URL = (
    "https://apix.cisco.com/software/suggestion/v2/"
    "suggestions/compatible/productId/{product_id}"
)
EOX_SERIAL_URL = (
    "https://apix.cisco.com/supporttools/eox/rest/5/"
    "EOXBySerialNumber/{page_index}/{serial_number}"
)
EOX_SOFTWARE_URL = (
    "https://apix.cisco.com/supporttools/eox/rest/5/"
    "EOXBySWReleaseString/{page_index}/"
)
MAX_PAGES = 100


class CiscoSupportAPIError(RuntimeError):
    """Safe Cisco failure carrying only a stable reason and HTTP status."""

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class CiscoSupportCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class CiscoAccessToken:
    value: str = field(repr=False)
    expires_in: int


@dataclass(frozen=True)
class CiscoSoftwareSuggestion:
    product_id: str
    software_type: str | None
    release: str | None
    alternate_release: str | None
    release_date: str | None
    lifecycle: str | None
    suggested: bool


@dataclass(frozen=True)
class CiscoEoxRecord:
    product_id: str | None
    description: str | None
    bulletin_number: str | None
    bulletin_url: str | None
    announcement_date: str | None
    end_of_sale_date: str | None
    end_of_software_maintenance_date: str | None
    end_of_security_support_date: str | None
    last_date_of_support: str | None
    updated_date: str | None


@dataclass(frozen=True)
class CiscoEoxResult:
    records: tuple[CiscoEoxRecord, ...]
    error_id: str | None = None


class CiscoSupportClient:
    """Authenticate and probe Cisco Support APIs without exposing payloads."""

    def __init__(
        self,
        *,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        requester: Any = requests,
    ) -> None:
        self.timeout = (connect_timeout, read_timeout)
        self.requester = requester

    @staticmethod
    def _error_for_status(status_code: int) -> CiscoSupportAPIError:
        if status_code == 401:
            reason = "authentication_failed"
        elif status_code == 403:
            reason = "not_authorized"
        elif status_code >= 500:
            reason = "unavailable"
        else:
            reason = "request_failed"
        return CiscoSupportAPIError(reason, status_code=status_code)

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = self.requester.request(
                method,
                url,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.exceptions.SSLError:
            raise CiscoSupportAPIError("tls_validation_failed") from None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            raise CiscoSupportAPIError("connection_failed") from None
        except requests.RequestException:
            raise CiscoSupportAPIError("unavailable") from None

        if not 200 <= response.status_code < 300:
            raise self._error_for_status(response.status_code)
        return response

    @staticmethod
    def _json_object(response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            raise CiscoSupportAPIError("invalid_response") from None
        if not isinstance(payload, dict):
            raise CiscoSupportAPIError("invalid_response")
        return payload

    def authenticate(self, credentials: CiscoSupportCredentials) -> CiscoAccessToken:
        response = self._request(
            "POST",
            TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            },
        )
        payload = self._json_object(response)
        value = payload.get("access_token")
        token_type = payload.get("token_type")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(value, str)
            or not value
            or any(character.isspace() for character in value)
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or expires_in < 1
        ):
            raise CiscoSupportAPIError("invalid_response")
        return CiscoAccessToken(value=value, expires_in=expires_in)

    def probe_software_suggestion(self, token: CiscoAccessToken) -> None:
        self.software_suggestions(token, "CISCO2811", "15.1(4)M5")

    def probe_eox(self, token: CiscoAccessToken) -> None:
        response = self._request(
            "GET",
            EOX_PROBE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token.value}",
            },
        )
        self._eox_result([self._json_object(response)])

    @staticmethod
    def _text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @classmethod
    def _date_text(cls, value: object) -> str | None:
        if isinstance(value, dict):
            value = value.get("value")
        return cls._text(value)

    @staticmethod
    def _input(value: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > maximum
            or any(character.isspace() for character in value)
        ):
            raise CiscoSupportAPIError("invalid_request")
        return value

    @staticmethod
    def _page_count(payload: dict) -> int:
        pagination = payload.get("paginationResponseRecord")
        if not isinstance(pagination, dict):
            pagination = payload.get("PaginationResponseRecord")
        if not isinstance(pagination, dict):
            return 1
        value = pagination.get("lastIndex", pagination.get("LastIndex", 1))
        try:
            pages = int(value)
        except (TypeError, ValueError):
            raise CiscoSupportAPIError("invalid_response") from None
        if pages < 1 or pages > MAX_PAGES:
            raise CiscoSupportAPIError("invalid_response")
        return pages

    @classmethod
    def _software_rows(cls, payload: dict) -> list[dict]:
        status = cls._text(payload.get("status"))
        error = payload.get("errorDetailsResponse")
        if error is not None and not isinstance(error, dict):
            raise CiscoSupportAPIError("invalid_response")
        if isinstance(error, dict):
            error_code = cls._text(error.get("errorCode"))
            if error_code in {"S3_NO_REC_FOUND", "S3_NO_SOFT_AVL"}:
                return []
            raise CiscoSupportAPIError("request_failed")
        if status is not None and status.casefold() != "success":
            raise CiscoSupportAPIError("invalid_response")
        rows = payload.get("suggestions", [])
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) for row in rows
        ):
            raise CiscoSupportAPIError("invalid_response")
        return rows

    def software_suggestions(
        self,
        token: CiscoAccessToken,
        product_id: str,
        current_release: str,
    ) -> tuple[CiscoSoftwareSuggestion, ...]:
        product_id = self._input(product_id, 20)
        current_release = self._input(current_release, 15)
        encoded_pid = quote(product_id, safe="")
        suggestions: list[CiscoSoftwareSuggestion] = []
        page_index = 1
        while True:
            response = self._request(
                "GET",
                SOFTWARE_COMPATIBLE_URL.format(product_id=encoded_pid),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token.value}",
                },
                params={
                    "currentRelease": current_release,
                    "pageIndex": page_index,
                },
            )
            payload = self._json_object(response)
            rows = self._software_rows(payload)
            for row in rows:
                base_pid = self._text(row.get("basePID"))
                if base_pid is None:
                    raise CiscoSupportAPIError("invalid_response")
                suggestions.append(CiscoSoftwareSuggestion(
                    product_id=base_pid,
                    software_type=self._text(row.get("softwareType")),
                    release=self._text(row.get("releaseFormat1")),
                    alternate_release=self._text(row.get("releaseFormat2")),
                    release_date=self._text(row.get("releaseDate")),
                    lifecycle=self._text(row.get("releaseLifeCycle")),
                    suggested=self._text(row.get("isSuggested")) == "Y",
                ))
            if page_index >= self._page_count(payload):
                return tuple(suggestions)
            page_index += 1

    @classmethod
    def _eox_result(cls, payloads: list[dict]) -> CiscoEoxResult:
        records: list[CiscoEoxRecord] = []
        error_id = None
        for payload in payloads:
            error = payload.get("EOXError")
            if error is not None and not isinstance(error, dict):
                raise CiscoSupportAPIError("invalid_response")
            if isinstance(error, dict):
                error_id = cls._text(error.get("ErrorID")) or error_id
            rows = payload.get("EOXRecord", [])
            if rows is None:
                rows = []
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) for row in rows
            ):
                raise CiscoSupportAPIError("invalid_response")
            for row in rows:
                records.append(CiscoEoxRecord(
                    product_id=cls._text(row.get("EOLProductID")),
                    description=cls._text(row.get("ProductIDDescription")),
                    bulletin_number=cls._text(row.get("ProductBulletinNumber")),
                    bulletin_url=cls._text(row.get("LinkToProductBulletinURL")),
                    announcement_date=cls._date_text(
                        row.get("EOXExternalAnnouncementDate")
                    ),
                    end_of_sale_date=cls._date_text(row.get("EndOfSaleDate")),
                    end_of_software_maintenance_date=cls._date_text(
                        row.get("EndOfSWMaintenanceReleases")
                    ),
                    end_of_security_support_date=cls._date_text(
                        row.get("EndOfSecurityVulSupportDate")
                    ),
                    last_date_of_support=cls._date_text(
                        row.get("LastDateOfSupport")
                    ),
                    updated_date=cls._date_text(row.get("UpdatedTimeStamp")),
                ))
        if not records and error_id:
            if error_id == "SSA_ERR_034":
                raise CiscoSupportAPIError("authentication_failed")
            if error_id == "SSA_ERR_036":
                raise CiscoSupportAPIError("not_authorized")
            if error_id in {
                "SSA_GENERIC_ERR",
                "SSA_ERR_011",
                "SSA_ERR_012",
                "SSA_ERR_013",
                "SSA_ERR_014",
                "SSA_ERR_018",
                "SSA_ERR_033",
                "SSA_ERR_037",
            }:
                raise CiscoSupportAPIError("unavailable")
        return CiscoEoxResult(tuple(records), error_id)

    def _eox_pages(
        self,
        token: CiscoAccessToken,
        url_factory,
        *,
        params: dict[str, object],
    ) -> CiscoEoxResult:
        payloads: list[dict] = []
        page_index = 1
        while True:
            response = self._request(
                "GET",
                url_factory(page_index),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token.value}",
                },
                params={**params, "responseencoding": "json"},
            )
            payload = self._json_object(response)
            payloads.append(payload)
            if page_index >= self._page_count(payload):
                return self._eox_result(payloads)
            page_index += 1

    def eox_by_serial(
        self,
        token: CiscoAccessToken,
        serial_number: str,
    ) -> CiscoEoxResult:
        serial_number = self._input(serial_number, 40)
        encoded_serial = quote(serial_number, safe="")
        return self._eox_pages(
            token,
            lambda page: EOX_SERIAL_URL.format(
                page_index=page,
                serial_number=encoded_serial,
            ),
            params={},
        )

    def eox_by_software(
        self,
        token: CiscoAccessToken,
        release: str,
        os_type: str | None,
    ) -> CiscoEoxResult:
        release = self._input(release, 200)
        if os_type is not None and os_type not in {
            "IOS",
            "IOS-XE",
            "NX-OS",
            "ASA",
        }:
            raise CiscoSupportAPIError("invalid_request")
        input_value = release if os_type is None else f"{release},{os_type}"
        return self._eox_pages(
            token,
            lambda page: EOX_SOFTWARE_URL.format(page_index=page),
            params={"input1": input_value},
        )


__all__ = [
    "CiscoAccessToken",
    "CiscoEoxRecord",
    "CiscoEoxResult",
    "CiscoSoftwareSuggestion",
    "CiscoSupportAPIError",
    "CiscoSupportClient",
    "CiscoSupportCredentials",
    "EOX_PROBE_URL",
    "EOX_SERIAL_URL",
    "EOX_SOFTWARE_URL",
    "SOFTWARE_COMPATIBLE_URL",
    "TOKEN_URL",
]
