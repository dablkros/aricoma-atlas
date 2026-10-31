"""Narrow client and orchestration service for the isolated VPN worker."""

from __future__ import annotations

from pathlib import Path
import re

import requests

from atlas.vpn.models import VPNConnectivityTestResult
from atlas.vpn.service import VPNRegistryError


class VPNWorkerError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class VPNWorkerClient:
    """Call only the typed connectivity-test operation exposed by the worker."""

    def __init__(self, base_url: str, token_file: Path, *, timeout: float = 75.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_file = Path(token_file)
        self.timeout = timeout

    def _token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise VPNWorkerError("vpn_worker_unavailable") from None
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise VPNWorkerError("vpn_worker_unavailable")
        return token

    def test(self, payload: dict) -> dict:
        try:
            response = requests.post(
                f"{self.base_url}/v1/test",
                json=payload,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise VPNWorkerError("vpn_worker_unavailable") from None
        try:
            data = response.json()
        except ValueError:
            raise VPNWorkerError("vpn_worker_invalid_response") from None
        if response.status_code == 409:
            raise VPNWorkerError("vpn_test_busy", 409)
        if response.status_code != 200 or not isinstance(data, dict):
            raise VPNWorkerError("vpn_worker_failed")
        return data


class VPNConnectivityTestService:
    """Validate NetBox ownership/mapping before asking the privileged worker."""

    def __init__(self, registry, worker: VPNWorkerClient) -> None:
        self.registry = registry
        self.worker = worker

    def run(self, connection_id: str, *, device_id: int, target_port: int) -> VPNConnectivityTestResult:
        connection = self.registry.get(connection_id)
        if connection.administrative_status == "disabled":
            raise VPNWorkerError("connection_disabled", 409)
        supported = {
            ("strongswan", "ipsec-ikev2"),
            ("openfortivpn", "ssl-vpn"),
        }
        if (connection.driver, connection.protocol) not in supported:
            raise VPNWorkerError("vpn_driver_not_testable", 409)
        try:
            resolved = self.registry.resolve_device(
                device_id, expected_tenant=connection.tenant_id,
            )
        except VPNRegistryError:
            raise
        if resolved.get("resolution_status") != "resolved" or resolved.get("connection_id") != connection_id:
            raise VPNWorkerError("vpn_test_target_not_assigned", 409)
        payload = {
            "connection_id": connection.connection_id,
            "tenant_id": connection.tenant_id,
            "secret_ref": connection.secret_ref,
            "gateway_host": connection.gateway_host,
            "gateway_port": connection.gateway_port or 500,
            "driver": connection.driver,
            "protocol": connection.protocol,
            "target_ip": resolved["management_ip"],
            "target_port": target_port,
        }
        result = self.worker.test(payload)
        result.update({
            "connection_id": connection_id,
            "device_id": device_id,
            "device_name": resolved.get("device_name"),
            "target_ip": resolved["management_ip"],
            "target_port": target_port,
        })
        try:
            return VPNConnectivityTestResult.model_validate(result)
        except Exception:
            raise VPNWorkerError("vpn_worker_invalid_response") from None


__all__ = ["VPNConnectivityTestService", "VPNWorkerClient", "VPNWorkerError"]
