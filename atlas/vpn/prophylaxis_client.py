"""Clients for VPN sessions and the unprivileged collector namespace."""

from __future__ import annotations

from pathlib import Path
import re

import requests

from atlas.prophylaxis.models import RawCollectorResult


class VPNProphylaxisError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Client:
    def __init__(self, base_url: str, token_file: Path, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token_file = Path(token_file)
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise VPNProphylaxisError("vpn_worker_unavailable") from None
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise VPNProphylaxisError("vpn_worker_unavailable")
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        try:
            response = requests.request(
                method, f"{self.base_url}{path}", headers=self._headers(),
                json=json, timeout=self.timeout,
            )
            data = response.json()
        except (requests.RequestException, ValueError):
            raise VPNProphylaxisError("vpn_worker_unavailable") from None
        if response.status_code >= 400 or not isinstance(data, dict):
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            raise VPNProphylaxisError(code or "vpn_worker_failed")
        return data


class VPNSessionClient(_Client):
    def open(self, connection, *, target_ips: tuple[str, ...] = ()) -> str:
        if connection.administrative_status == "disabled":
            raise VPNProphylaxisError("connection_disabled")
        data = self._request("POST", "/v1/session/open", json={
            "connection_id": connection.connection_id,
            "tenant_id": connection.tenant_id,
            "secret_ref": connection.secret_ref,
            "gateway_host": connection.gateway_host,
            "gateway_port": connection.gateway_port or 1194,
            "driver": connection.driver,
            "protocol": connection.protocol,
            "external_mfa": connection.auth_method == "external_mfa",
            "target_ips": list(target_ips),
        })
        session_id = data.get("session_id")
        if not isinstance(session_id, str):
            raise VPNProphylaxisError("vpn_worker_invalid_response")
        return session_id

    def close(self, session_id: str) -> None:
        self._request("DELETE", f"/v1/session/{session_id}")


class VPNAnsibleBackend(_Client):
    def execute(self, *, host, port, profile, collector_id=None, check_id,
                credentials, telnet_fallback_port=None, device_model=None):
        data = self._request("POST", "/v1/execute", json={
            "host": host, "port": port, "platform": profile.slug,
            "transport": profile.transport,
            "collector_id": (collector_id or "device_facts"),
            "check_id": check_id,
            "credentials": credentials,
            "telnet_fallback_port": telnet_fallback_port,
            "device_model": device_model,
        })
        return RawCollectorResult.model_validate(data)


__all__ = ["VPNAnsibleBackend", "VPNProphylaxisError", "VPNSessionClient"]
