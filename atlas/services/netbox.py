"""Read-only NetBox API client backed by the OpenBao token source."""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests

from atlas.services.errors import DependencyError
from atlas.services.openbao import OpenBaoService


class NetBoxService:
    def __init__(
        self,
        base_url: str,
        openbao: OpenBaoService,
        connect_timeout: float,
        read_timeout: float,
        requester=None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.openbao = openbao
        self.timeout: Tuple[float, float] = (connect_timeout, read_timeout)
        self.requester = requester or requests
        self._base_origin = self._origin(self.base_url)

    @staticmethod
    def _origin(url: str) -> Tuple[str, str, int]:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    @staticmethod
    def authorization_headers(token: str) -> Dict[str, str]:
        if (
            not isinstance(token, str)
            or token != token.strip()
            or not token
            or any(character.isspace() for character in token)
        ):
            raise DependencyError("authentication_failed")
        scheme = "Bearer" if token.startswith("nbt_") else "Token"
        return {
            "Authorization": f"{scheme} {token}",
            "Accept": "application/json",
        }

    def _endpoint_url(self, endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme or parsed.netloc or not endpoint.startswith("/api/"):
            raise DependencyError("invalid_response")
        return urljoin(f"{self.base_url}/", endpoint.lstrip("/"))

    def _next_url(self, current_url: str, next_url: str) -> str:
        candidate = urljoin(current_url, next_url)
        parsed = urlsplit(candidate)
        if (
            self._origin(candidate) != self._base_origin
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/api/")
        ):
            raise DependencyError("invalid_response")
        return candidate

    def _request_json(
        self,
        url: str,
        token: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            response = self.requester.get(
                url,
                headers=self.authorization_headers(token),
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise DependencyError("connection_failed") from None

        if response.status_code in {401, 403}:
            raise DependencyError("authentication_failed")
        if response.status_code < 200 or response.status_code >= 300:
            raise DependencyError("unavailable")

        try:
            data = response.json()
        except ValueError:
            raise DependencyError("invalid_response") from None
        if not isinstance(data, dict):
            raise DependencyError("invalid_response")
        return data

    @staticmethod
    def _validate_page(data: Dict[str, Any]) -> None:
        if not isinstance(data.get("results"), list):
            raise DependencyError("invalid_response")
        if data.get("next") is not None and not isinstance(data["next"], str):
            raise DependencyError("invalid_response")

    def check_ready(self) -> None:
        token = self.openbao.get_netbox_api_token()
        data = self._request_json(
            self._endpoint_url("/api/dcim/devices/"),
            token,
            params={"limit": 1},
        )
        self._validate_page(data)

    def get_oxidized_devices(self) -> List[dict]:
        """Return active, explicitly enabled devices using NetBox pagination."""
        return self.get_all(
            "/api/dcim/devices/",
            params={
                "cf_oxidized_enabled": "true",
                "status": "active",
            },
        )

    def get_prophylaxis_devices(self) -> List[dict]:
        """Return active devices explicitly enabled for Profylaxia."""
        return self.get_all(
            "/api/dcim/devices/",
            params={
                "cf_profylaxia_enabled": "true",
                "status": "active",
            },
        )

    def get_device_services(self, device_id: int, name: str) -> List[dict]:
        """Return an exact named application service assigned to one device."""
        return self.get_all(
            "/api/ipam/services/",
            params={
                "device_id": device_id,
                "name": name,
            },
        )

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> dict:
        token = self.openbao.get_netbox_api_token()
        return self._request_json(self._endpoint_url(endpoint), token, params=params)

    def get_all(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[dict]:
        token = self.openbao.get_netbox_api_token()
        url = self._endpoint_url(endpoint)
        query = dict(params or {})
        results: List[dict] = []
        seen = set()

        while url:
            if url in seen:
                raise DependencyError("invalid_response")
            seen.add(url)
            page = self._request_json(url, token, params=query or None)
            self._validate_page(page)
            if any(not isinstance(item, dict) for item in page["results"]):
                raise DependencyError("invalid_response")
            results.extend(page["results"])
            next_url = page.get("next")
            url = self._next_url(url, next_url) if next_url else ""
            query = {}

        return results


__all__ = ["NetBoxService"]
