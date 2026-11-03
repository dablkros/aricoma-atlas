"""Narrow client and orchestration service for the isolated VPN worker."""

from __future__ import annotations

from pathlib import Path
import logging
import re

import requests

from atlas.vpn.models import VPNConnectivityTestResult, VPNMFATestState
from atlas.vpn.service import VPNRegistryError


logger = logging.getLogger("atlas.vpn.worker_client")
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ERROR_STATUS = {
    "vpn_worker_authentication_failed": (401, 503),
    "vpn_test_busy": (409, 409),
    "vpn_worker_request_rejected": (422, 503),
    "vpn_worker_identity_unavailable": (503, 503),
}


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

    def test(self, payload: dict, *, request_id: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id):
            headers["X-Request-ID"] = request_id
        try:
            response = requests.post(
                f"{self.base_url}/v1/test",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning(
                "vpn.worker_request_failed",
                extra={
                    "event": "vpn.worker_request_failed",
                    "reason_code": "vpn_worker_unavailable",
                    "request_id": request_id,
                    "exception_type": type(exc).__name__,
                },
            )
            raise VPNWorkerError("vpn_worker_unavailable") from None
        try:
            data = response.json()
        except ValueError:
            raise VPNWorkerError("vpn_worker_invalid_response") from None
        error = data.get("error") if isinstance(data, dict) else None
        worker_code = error.get("code") if isinstance(error, dict) else None
        expected_status = _WORKER_ERROR_STATUS.get(worker_code)
        if expected_status is not None and response.status_code == expected_status[0]:
            status_code = expected_status[1]
            logger.warning(
                "vpn.worker_response_rejected",
                extra={
                    "event": "vpn.worker_response_rejected",
                    "request_id": request_id,
                    "reason_code": worker_code,
                    "worker_status_code": response.status_code,
                },
            )
            raise VPNWorkerError(worker_code, status_code)
        if response.status_code != 200 or not isinstance(data, dict):
            logger.warning(
                "vpn.worker_response_failed",
                extra={
                    "event": "vpn.worker_response_failed",
                    "request_id": request_id,
                    "reason_code": "vpn_worker_failed",
                    "worker_status_code": response.status_code,
                },
            )
            raise VPNWorkerError("vpn_worker_failed")
        return data

    def _mfa_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        request_id: str | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id):
            headers["X-Request-ID"] = request_id
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
                timeout=min(self.timeout, 15.0),
            )
        except requests.RequestException:
            raise VPNWorkerError("vpn_worker_unavailable") from None
        try:
            data = response.json()
        except ValueError:
            raise VPNWorkerError("vpn_worker_invalid_response") from None
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if response.status_code >= 400:
            allowed = {
                "vpn_test_busy", "vpn_mfa_operation_not_found", "vpn_mfa_not_waiting",
                "vpn_worker_request_rejected", "vpn_worker_identity_unavailable",
            }
            raise VPNWorkerError(code if code in allowed else "vpn_worker_failed", response.status_code)
        if not isinstance(data, dict):
            raise VPNWorkerError("vpn_worker_invalid_response")
        return data

    def start_mfa(self, payload: dict, *, request_id: str | None = None) -> dict:
        return self._mfa_request("POST", "/v1/test/mfa", payload=payload, request_id=request_id)

    def mfa_status(self, operation_id: str, *, request_id: str | None = None) -> dict:
        return self._mfa_request("GET", f"/v1/test/mfa/{operation_id}", request_id=request_id)

    def submit_mfa(
        self,
        operation_id: str,
        connection_id: str,
        code: str,
        *,
        request_id: str | None = None,
    ) -> dict:
        return self._mfa_request(
            "POST", f"/v1/test/mfa/{operation_id}/otp",
            payload={"connection_id": connection_id, "code": code},
            request_id=request_id,
        )


class VPNConnectivityTestService:
    """Validate NetBox ownership/mapping before asking the privileged worker."""

    def __init__(self, registry, worker: VPNWorkerClient) -> None:
        self.registry = registry
        self.worker = worker

    @staticmethod
    def _mfa_mode(auth_method: str) -> str:
        return {
            "external_mfa": "auto",
            "external_mfa_push": "push",
            "external_mfa_otp": "otp",
        }.get(auth_method, "none")

    def _payload(
        self,
        connection_id: str,
        *,
        device_id: int,
        target_port: int,
    ) -> tuple[dict, dict]:
        connection = self.registry.get(connection_id)
        if connection.administrative_status == "disabled":
            raise VPNWorkerError("connection_disabled", 409)
        supported = {
            ("strongswan", "ipsec-ikev2"),
            ("openfortivpn", "ssl-vpn"),
            ("openvpn", "openvpn"),
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
        mfa_mode = self._mfa_mode(connection.auth_method)
        payload = {
            "connection_id": connection.connection_id,
            "tenant_id": connection.tenant_id,
            "secret_ref": connection.secret_ref,
            "gateway_host": connection.gateway_host,
            "gateway_port": connection.gateway_port or 500,
            "driver": connection.driver,
            "protocol": connection.protocol,
            "external_mfa": mfa_mode != "none",
            "mfa_mode": mfa_mode,
            "target_ip": resolved["management_ip"],
            "target_port": target_port,
        }
        return payload, resolved

    def run(
        self,
        connection_id: str,
        *,
        device_id: int,
        target_port: int,
        request_id: str | None = None,
    ) -> VPNConnectivityTestResult:
        payload, resolved = self._payload(
            connection_id, device_id=device_id, target_port=target_port,
        )
        result = self.worker.test(payload, request_id=request_id)
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

    def start_mfa(
        self,
        connection_id: str,
        *,
        device_id: int,
        target_port: int,
        request_id: str | None = None,
    ) -> VPNMFATestState:
        payload, _resolved = self._payload(
            connection_id, device_id=device_id, target_port=target_port,
        )
        if not payload["external_mfa"] or payload["driver"] != "strongswan":
            raise VPNWorkerError("vpn_mfa_not_configured", 409)
        try:
            return VPNMFATestState.model_validate(
                self.worker.start_mfa(payload, request_id=request_id)
            )
        except VPNWorkerError:
            raise
        except Exception:
            raise VPNWorkerError("vpn_worker_invalid_response") from None

    def mfa_status(
        self,
        connection_id: str,
        operation_id: str,
        *,
        device_id: int,
        target_port: int,
        request_id: str | None = None,
    ) -> VPNMFATestState:
        _payload, resolved = self._payload(
            connection_id, device_id=device_id, target_port=target_port,
        )
        data = self.worker.mfa_status(operation_id, request_id=request_id)
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            result.update({
                "connection_id": connection_id,
                "device_id": device_id,
                "device_name": resolved.get("device_name"),
                "target_ip": resolved["management_ip"],
                "target_port": target_port,
            })
        try:
            return VPNMFATestState.model_validate(data)
        except Exception:
            raise VPNWorkerError("vpn_worker_invalid_response") from None

    def submit_mfa(
        self,
        connection_id: str,
        operation_id: str,
        code: str,
        *,
        device_id: int,
        target_port: int,
        request_id: str | None = None,
    ) -> VPNMFATestState:
        payload, _resolved = self._payload(
            connection_id, device_id=device_id, target_port=target_port,
        )
        if not payload["external_mfa"] or payload["driver"] != "strongswan":
            raise VPNWorkerError("vpn_mfa_not_configured", 409)
        try:
            return VPNMFATestState.model_validate(
                self.worker.submit_mfa(
                    operation_id, connection_id, code, request_id=request_id,
                )
            )
        except VPNWorkerError:
            raise
        except Exception:
            raise VPNWorkerError("vpn_worker_invalid_response") from None


__all__ = ["VPNConnectivityTestService", "VPNWorkerClient", "VPNWorkerError"]
