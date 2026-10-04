"""Oxidized API connectivity and explicit node-operation service."""

import time
from typing import List, Tuple
from urllib.parse import quote, urljoin

import requests

from atlas.services.errors import DependencyError


class OxidizedService:
    def __init__(
        self,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        requester=None,
        sleeper=None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout: Tuple[float, float] = (connect_timeout, read_timeout)
        self.requester = requester or requests
        self.sleeper = sleeper or time.sleep

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
        deadline = time.monotonic() + max(self.timeout[1], 1.0)
        waiting_for_native = False

        while True:
            try:
                response = self.requester.get(
                    urljoin(f"{self.base_url}/", "reload?format=json"),
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                )
            except requests.RequestException:
                # The empty-inventory waiting server exits before native
                # Oxidized binds the same port. Retry only after that state was
                # explicitly observed; ordinary connectivity failures remain
                # immediate and distinguishable.
                if waiting_for_native and time.monotonic() < deadline:
                    self.sleeper(0.5)
                    continue
                raise DependencyError("connection_failed") from None

            if inventory and response.status_code == 503:
                try:
                    waiting = response.json()
                except ValueError:
                    waiting = None
                if (
                    waiting == {"status": "waiting_for_inventory"}
                    and time.monotonic() < deadline
                ):
                    waiting_for_native = True
                    self.sleeper(0.5)
                    continue
            break

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

    def queue_backup(self, device: str) -> None:
        encoded_device = quote(device, safe="")
        try:
            response = self.requester.get(
                f"{self.base_url}/node/next/{encoded_device}",
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise DependencyError("connection_failed") from None

        if response.status_code in {401, 403}:
            raise DependencyError("authentication_failed")
        if response.status_code < 200 or response.status_code >= 300:
            raise DependencyError("unavailable")


__all__ = ["OxidizedService"]
