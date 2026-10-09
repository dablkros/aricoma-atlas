"""Secret-free public schemas for Atlas-managed Zabbix data."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class ZabbixSyncIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Optional[str]
    code: str


class ZabbixSyncSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    created: int
    updated: int
    disabled: int
    unchanged: int
    errors: int


class ZabbixSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "partial_success"]
    summary: ZabbixSyncSummaryResponse
    created: List[str]
    updated: List[str]
    disabled: List[str]
    unchanged: List[str]
    issues: List[ZabbixSyncIssueResponse]


class ZabbixStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["healthy"]
    version: str
    managed_hosts: int


class ZabbixHostResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: str
    netbox_id: int
    name: str
    enabled: bool
    ip: Optional[str]
    availability: Literal["available", "unavailable", "unknown"]


class ZabbixProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    name: str
    severity: int
    timestamp: int
    acknowledged: bool


class MetricPointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: int
    value: float


class MetricResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    unit: str
    value: Optional[float]
    points: List[MetricPointResponse]


class DeviceMonitoringResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: int
    name: str
    status: Literal["up", "down", "unknown", "disabled"]
    problems: List[ZabbixProblemResponse]
    metrics: dict[str, Optional[float]]
