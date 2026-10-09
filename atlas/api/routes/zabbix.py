"""Atlas-owned HTTP boundary for Zabbix monitoring data and reconciliation."""

from fastapi import APIRouter, Query, Request, status

from atlas.api.errors import AtlasError
from atlas.api.schemas.zabbix import (
    DeviceMonitoringResponse,
    MetricResponse,
    ZabbixHostResponse,
    ZabbixProblemResponse,
    ZabbixStatusResponse,
    ZabbixSyncResponse,
)
from atlas.integrations.zabbix import ZabbixAPIError
from atlas.services.errors import DependencyError
from atlas.services.zabbix import NETBOX_ID_TAG, translate_zabbix_error
from atlas.services.zabbix_sync import ZabbixSyncFailure


router = APIRouter(tags=["zabbix"])


def _failure(exc) -> AtlasError:
    code = exc.reason if isinstance(exc, DependencyError) else getattr(exc, "code", "unavailable")
    status_code = getattr(exc, "status_code", 503)
    return AtlasError(status_code, f"zabbix_{code}", "Zabbix operation failed")


def _problem(item: dict) -> ZabbixProblemResponse:
    try:
        return ZabbixProblemResponse(
            event_id=str(item["eventid"]),
            name=str(item["name"]),
            severity=int(item["severity"]),
            timestamp=int(item["clock"]),
            acknowledged=str(item.get("acknowledged", "0")) == "1",
        )
    except (KeyError, TypeError, ValueError):
        raise AtlasError(502, "zabbix_invalid_response", "Zabbix returned invalid data") from None


def _host(item: dict, monitoring) -> ZabbixHostResponse:
    tags = monitoring._tags(item)
    interfaces = item.get("interfaces") or []
    interface = next((x for x in interfaces if isinstance(x, dict) and str(x.get("type", "2")) == "2"), None)
    availability_value = str(interface.get("available", "0")) if interface else "0"
    availability = {"1": "available", "2": "unavailable"}.get(availability_value, "unknown")
    try:
        netbox_id = int(tags[NETBOX_ID_TAG])
    except (KeyError, TypeError, ValueError):
        raise AtlasError(502, "zabbix_invalid_response", "Zabbix returned invalid data") from None
    return ZabbixHostResponse(
        host_id=str(item["hostid"]),
        netbox_id=netbox_id,
        name=str(item.get("name") or item.get("host") or ""),
        enabled=str(item.get("status")) == "0",
        ip=str(interface.get("ip")) if interface and interface.get("ip") else None,
        availability=availability,
    )


@router.post("/zabbix/sync", response_model=ZabbixSyncResponse)
def sync_zabbix(request: Request) -> ZabbixSyncResponse:
    try:
        result = request.app.state.dependencies.zabbix_sync.sync()
    except ZabbixSyncFailure as exc:
        raise _failure(exc) from None
    return ZabbixSyncResponse(
        status=result.status,
        summary=result.summary.__dict__,
        created=list(result.created),
        updated=list(result.updated),
        disabled=list(result.disabled),
        unchanged=list(result.unchanged),
        issues=[{"device": item.device, "code": item.code} for item in result.issues],
    )


@router.get("/zabbix/status", response_model=ZabbixStatusResponse)
def zabbix_status(request: Request) -> ZabbixStatusResponse:
    service = request.app.state.dependencies.zabbix
    monitoring = request.app.state.dependencies.zabbix_monitoring
    try:
        client = service.client()
        version = client.api_version()
        client.host_get({"output": ["hostid"], "limit": 1})
        hosts = monitoring.hosts()
    except ZabbixAPIError as exc:
        raise _failure(translate_zabbix_error(exc)) from None
    except DependencyError as exc:
        raise _failure(exc) from None
    return ZabbixStatusResponse(status="healthy", version=version, managed_hosts=len(hosts))


@router.get("/zabbix/hosts", response_model=list[ZabbixHostResponse])
def zabbix_hosts(request: Request) -> list[ZabbixHostResponse]:
    monitoring = request.app.state.dependencies.zabbix_monitoring
    try:
        return [_host(item, monitoring) for item in monitoring.hosts()]
    except DependencyError as exc:
        raise _failure(exc) from None


@router.get("/zabbix/problems", response_model=list[ZabbixProblemResponse])
def zabbix_problems(request: Request) -> list[ZabbixProblemResponse]:
    try:
        return [_problem(item) for item in request.app.state.dependencies.zabbix_monitoring.problems()]
    except DependencyError as exc:
        raise _failure(exc) from None


@router.get("/devices/{device_id}/monitoring", response_model=DeviceMonitoringResponse)
def device_monitoring(device_id: int, request: Request) -> DeviceMonitoringResponse:
    monitoring = request.app.state.dependencies.zabbix_monitoring
    try:
        host = monitoring.host_for_netbox_id(device_id)
        if host is None:
            raise AtlasError(404, "monitoring_host_not_found", "Monitoring host not found")
        exposed = _host(host, monitoring)
        problems = [_problem(item) for item in monitoring.problems([exposed.host_id])]
        cpu = monitoring.metric(exposed.host_id, "cpu", "1h")
    except DependencyError as exc:
        raise _failure(exc) from None
    if not exposed.enabled:
        state = "disabled"
    else:
        state = {"available": "up", "unavailable": "down"}.get(exposed.availability, "unknown")
    return DeviceMonitoringResponse(
        device_id=device_id,
        name=exposed.name,
        status=state,
        problems=problems,
        metrics={"cpu": cpu["value"]},
    )


@router.get("/devices/{device_id}/monitoring/metrics/{metric}", response_model=MetricResponse)
def device_metric(
    device_id: int,
    metric: str,
    request: Request,
    window: str = Query(default="1h", pattern="^(1h|24h|7d)$"),
) -> MetricResponse:
    monitoring = request.app.state.dependencies.zabbix_monitoring
    try:
        host = monitoring.host_for_netbox_id(device_id)
        if host is None:
            raise AtlasError(404, "monitoring_host_not_found", "Monitoring host not found")
        result = monitoring.metric(str(host["hostid"]), metric, window)
    except ValueError:
        raise AtlasError(404, "monitoring_metric_not_supported", "Monitoring metric not supported") from None
    except DependencyError as exc:
        raise _failure(exc) from None
    return MetricResponse(
        metric=result["metric"],
        unit=result["unit"],
        value=result["value"],
        points=[{"timestamp": item.timestamp, "value": item.value} for item in result["points"]],
    )
