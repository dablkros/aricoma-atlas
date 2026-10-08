"""Zabbix dependency and read-model operations for the Atlas API."""

import time
from dataclasses import dataclass
from typing import Optional

from atlas.integrations.zabbix import ZabbixAPIError, ZabbixClient
from atlas.services.errors import DependencyError
from atlas.zabbix_config import load_zabbix_config


MANAGED_BY_TAG = "managed_by"
MANAGED_BY_VALUE = "aricoma-atlas"
NETBOX_ID_TAG = "netbox_id"


def translate_zabbix_error(exc: ZabbixAPIError) -> DependencyError:
    if exc.code == "connection_failed":
        return DependencyError("connection_failed")
    if exc.code == "authentication_failed":
        return DependencyError("authentication_failed")
    if exc.code == "invalid_response":
        return DependencyError("invalid_response")
    return DependencyError("unavailable")


class ZabbixService:
    def __init__(
        self,
        api_url: str,
        openbao,
        connect_timeout: float,
        read_timeout: float,
        client_factory=ZabbixClient,
    ) -> None:
        self.api_url = api_url
        self.openbao = openbao
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.client_factory = client_factory

    def client(self) -> ZabbixClient:
        token = self.openbao.get_zabbix_api_token()
        return self.client_factory(
            self.api_url,
            token,
            self.connect_timeout,
            self.read_timeout,
        )

    def check_ready(self) -> None:
        try:
            client = self.client()
            client.api_version()
            client.host_get({"output": ["hostid"], "limit": 1})
        except ZabbixAPIError as exc:
            raise translate_zabbix_error(exc) from exc


@dataclass(frozen=True)
class MetricPoint:
    timestamp: int
    value: float


class ZabbixMonitoringService:
    def __init__(self, zabbix: ZabbixService, config=None) -> None:
        self.zabbix = zabbix
        self.config = config or load_zabbix_config()

    @staticmethod
    def _tags(host: dict) -> dict:
        tags = host.get("tags")
        if not isinstance(tags, list):
            return {}
        return {
            item.get("tag"): item.get("value")
            for item in tags
            if isinstance(item, dict) and isinstance(item.get("tag"), str)
        }

    def hosts(self) -> list[dict]:
        try:
            return self.zabbix.client().host_get(
                {
                    "output": ["hostid", "host", "name", "status"],
                    "selectInterfaces": ["interfaceid", "ip", "available"],
                    "selectTags": "extend",
                    "tags": [
                        {
                            "tag": MANAGED_BY_TAG,
                            "value": MANAGED_BY_VALUE,
                            "operator": 1,
                        }
                    ],
                }
            )
        except ZabbixAPIError as exc:
            raise translate_zabbix_error(exc) from exc

    def host_for_netbox_id(self, device_id: int) -> Optional[dict]:
        for host in self.hosts():
            if self._tags(host).get(NETBOX_ID_TAG) == str(device_id):
                return host
        return None

    def problems(self, hostids=None) -> list[dict]:
        params = {
            "output": ["eventid", "name", "severity", "clock", "acknowledged"],
            "recent": True,
            "sortfield": ["eventid"],
            "sortorder": "DESC",
        }
        if hostids:
            params["hostids"] = list(hostids)
        try:
            return self.zabbix.client().problem_get(params)
        except ZabbixAPIError as exc:
            raise translate_zabbix_error(exc) from exc

    def _metric_item(self, hostid: str, metric: str) -> Optional[dict]:
        prefixes = self.config["metric_keys"].get(metric)
        if not prefixes:
            raise ValueError("unsupported_metric")
        client = self.zabbix.client()
        candidates = []
        try:
            for prefix in prefixes:
                candidates.extend(
                    client.item_get(
                        {
                            "output": [
                                "itemid", "key_", "name", "value_type", "units",
                                "lastvalue", "lastclock", "status", "state",
                            ],
                            "hostids": [hostid],
                            "search": {"key_": prefix},
                            "searchWildcardsEnabled": True,
                            "filter": {"status": "0", "state": "0"},
                        }
                    )
                )
        except ZabbixAPIError as exc:
            raise translate_zabbix_error(exc) from exc
        numeric = [
            item for item in candidates
            if str(item.get("value_type")) in {"0", "3"}
            and str(item.get("lastclock", "0")).isdigit()
        ]
        if not numeric:
            return None
        return max(numeric, key=lambda item: int(item.get("lastclock", 0)))

    def metric(self, hostid: str, metric: str, window: str) -> dict:
        item = self._metric_item(hostid, metric)
        if item is None:
            return {"metric": metric, "unit": "", "value": None, "points": []}
        durations = {"1h": 3600, "24h": 86400, "7d": 604800}
        if window not in durations:
            raise ValueError("unsupported_window")
        now = int(time.time())
        params = {
            "output": "extend",
            "itemids": [item["itemid"]],
            "time_from": now - durations[window],
            "time_till": now,
            "sortfield": "clock",
            "sortorder": "ASC",
        }
        try:
            client = self.zabbix.client()
            if window == "7d":
                rows = client.trend_get(params)
                points = [
                    MetricPoint(int(row["clock"]), float(row["value_avg"]))
                    for row in rows
                ]
            else:
                params["history"] = int(item["value_type"])
                rows = client.history_get(params)
                points = [
                    MetricPoint(int(row["clock"]), float(row["value"]))
                    for row in rows
                ]
        except (KeyError, TypeError, ValueError):
            raise DependencyError("invalid_response") from None
        except ZabbixAPIError as exc:
            raise translate_zabbix_error(exc) from exc
        try:
            current = float(item["lastvalue"]) if item.get("lastclock") else None
        except (TypeError, ValueError):
            current = None
        return {
            "metric": metric,
            "unit": str(item.get("units") or ""),
            "value": current,
            "points": points,
        }


__all__ = [
    "MANAGED_BY_TAG",
    "MANAGED_BY_VALUE",
    "NETBOX_ID_TAG",
    "MetricPoint",
    "ZabbixMonitoringService",
    "ZabbixService",
    "translate_zabbix_error",
]
