"""Read-only Oxidized API connectivity service."""

from typing import List, Tuple
from urllib.parse import urljoin

import requests

from atlas.services.errors import DependencyError


class OxidizedService:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        requester=None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout: Tuple[float, float] = (connect_timeout, read_timeout)
        self.requester = requester or requests

    def get_nodes(self) -> List[dict]:
        try:
            response = self.requester.get(
                urljoin(f"{self.base_url}/", "nodes.json"),
                headers={"Accept": "application/json"},
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
        if not isinstance(data, list) or any(
            not isinstance(node, dict) for node in data
        ):
            raise DependencyError("invalid_response")
        return data

    def check_ready(self) -> None:
        self.get_nodes()

    def reload_inventory(self, inventory: List[dict]) -> None:
        try:
            response = self.requester.get(
                urljoin(f"{self.base_url}/", "reload?format=json"),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise DependencyError("connection_failed") from None

        if not inventory and response.status_code == 503:
            return
        if response.status_code in {401, 403}:
            raise DependencyError("authentication_failed")
        if response.status_code < 200 or response.status_code >= 300:
            raise DependencyError("unavailable")
        try:
            data = response.json()
        except ValueError:
            raise DependencyError("invalid_response") from None
        if data != ["reloaded list of nodes"]:
            raise DependencyError("invalid_response")


__all__ = ["OxidizedService"]
