"""Strict Zabbix JSON-RPC client with a secret-safe error contract."""

import itertools
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests


class ZabbixAPIError(RuntimeError):
    """Expected integration failure that never includes response or secret data."""

    SAFE_CODES = {
        "connection_failed",
        "authentication_failed",
        "http_error",
        "invalid_response",
        "api_error",
    }

    def __init__(self, code: str) -> None:
        if code not in self.SAFE_CODES:
            raise ValueError("Unsupported Zabbix failure code")
        self.code = code
        super().__init__(code)


class ZabbixClient:
    def __init__(
        self,
        api_url: str,
        token: Optional[str],
        connect_timeout: float,
        read_timeout: float,
        requester=None,
        auth_mode: str = "bearer",
    ) -> None:
        parsed = urlsplit(str(api_url))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid Zabbix API URL")
        if token is not None and (
            not isinstance(token, str)
            or not token
            or token != token.strip()
            or any(character.isspace() for character in token)
        ):
            raise ValueError("Invalid Zabbix API token")
        if auth_mode not in {"bearer", "session"}:
            raise ValueError("Invalid Zabbix authentication mode")
        self.api_url = str(api_url)
        self._token = token
        self._auth_mode = auth_mode
        self.timeout: Tuple[float, float] = (connect_timeout, read_timeout)
        self.requester = requester or requests
        self._request_ids = itertools.count(1)

    def call(
        self,
        method: str,
        params: Optional[Any] = None,
        *,
        authenticated: bool = True,
    ) -> Any:
        if not isinstance(method, str) or not method:
            raise ValueError("Zabbix method is required")
        if authenticated and not self._token:
            raise ZabbixAPIError("authentication_failed")

        request_id = next(self._request_ids)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json-rpc",
        }
        if authenticated and self._auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self._token}"
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": {} if params is None else params,
            "id": request_id,
        }
        if authenticated and self._auth_mode == "session":
            payload["auth"] = self._token
        try:
            response = self.requester.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise ZabbixAPIError("connection_failed") from None

        if response.status_code in {401, 403}:
            raise ZabbixAPIError("authentication_failed")
        if not 200 <= response.status_code < 300:
            raise ZabbixAPIError("http_error")
        try:
            body = response.json()
        except ValueError:
            raise ZabbixAPIError("invalid_response") from None
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or body.get("id") != request_id
        ):
            raise ZabbixAPIError("invalid_response")
        if "error" in body:
            error = body.get("error")
            data = error if isinstance(error, dict) else {}
            safe_text = " ".join(
                str(data.get(key, "")) for key in ("message", "data")
            ).lower()
            code = (
                "authentication_failed"
                if any(
                    phrase in safe_text
                    for phrase in (
                        "not authorized",
                        "not authorised",
                        "login name or password is incorrect",
                        "session terminated",
                        "re-login",
                    )
                )
                else "api_error"
            )
            raise ZabbixAPIError(code)
        if "result" not in body:
            raise ZabbixAPIError("invalid_response")
        return body["result"]

    def api_version(self) -> str:
        result = self.call("apiinfo.version", [], authenticated=False)
        if not isinstance(result, str) or not result:
            raise ZabbixAPIError("invalid_response")
        return result

    def host_get(self, params: Optional[Dict[str, Any]] = None) -> list:
        result = self.call("host.get", params or {})
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            raise ZabbixAPIError("invalid_response")
        return result

    def host_create(self, params: Dict[str, Any]) -> str:
        result = self.call("host.create", params)
        return self._single_id(result, "hostids")

    def host_update(self, params: Dict[str, Any]) -> str:
        result = self.call("host.update", params)
        return self._single_id(result, "hostids")

    def hostgroup_get(self, params: Optional[Dict[str, Any]] = None) -> list:
        result = self.call("hostgroup.get", params or {})
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    def hostgroup_create(self, name: str) -> str:
        return self._single_id(
            self.call("hostgroup.create", {"name": name}),
            "groupids",
        )

    def template_get(self, params: Optional[Dict[str, Any]] = None) -> list:
        result = self.call("template.get", params or {})
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    def item_get(self, params: Optional[Dict[str, Any]] = None) -> list:
        result = self.call("item.get", params or {})
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    def problem_get(self, params: Optional[Dict[str, Any]] = None) -> list:
        result = self.call("problem.get", params or {})
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    def history_get(self, params: Dict[str, Any]) -> list:
        result = self.call("history.get", params)
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    def trend_get(self, params: Dict[str, Any]) -> list:
        result = self.call("trend.get", params)
        if not isinstance(result, list):
            raise ZabbixAPIError("invalid_response")
        return result

    @staticmethod
    def _single_id(result: Any, field: str) -> str:
        values = result.get(field) if isinstance(result, dict) else None
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], str)
            or not values[0]
        ):
            raise ZabbixAPIError("invalid_response")
        return values[0]


__all__ = ["ZabbixAPIError", "ZabbixClient"]
