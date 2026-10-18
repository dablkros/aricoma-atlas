"""Authenticated monitoring routes. Targets and credentials cannot be caller supplied."""
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator
from atlas.monitoring.models import MonitoringError
from atlas.services.errors import DependencyError
from atlas.device_credentials import DeviceValidationError
from atlas.integrations.zabbix import ZabbixAPIError


logger = logging.getLogger("atlas.monitoring")


def authorized(request: Request) -> None:
    require_operator(request, scope="monitoring")


router = APIRouter(
    prefix="/monitoring",
    tags=["monitoring provisioning"],
    dependencies=[Depends(authorized)],
)


class Mode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["monitor_only", "provision_and_monitor"] = "monitor_only"


class Apply(Mode):
    plan_id: UUID
    confirmed: Literal[True]


class Bulk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plans: list[Apply] = Field(min_length=1, max_length=100)


def safe(operation):
    try:
        return operation()
    except MonitoringError as exc:
        logger.info(
            "monitoring.operation_blocked",
            extra={"event": "monitoring.operation_blocked", "error_code": exc.code},
        )
        raise AtlasError(exc.status, exc.code, "Monitoring operation cannot proceed; check configuration and plan") from None
    except DeviceValidationError as exc:
        logger.info(
            "monitoring.operation_blocked",
            extra={"event": "monitoring.operation_blocked", "error_code": exc.code},
        )
        raise AtlasError(422, exc.code, "Device credential validation failed") from None
    except (DependencyError, ZabbixAPIError):
        raise AtlasError(503, "monitoring_dependency_unavailable", "A monitoring dependency is unavailable") from None


@router.get("/devices")
def devices(request: Request):
    return safe(request.app.state.dependencies.monitoring.list_devices)


@router.post("/devices/{device_id}/inspect")
def inspect(device_id: int, body: Mode, request: Request):
    return safe(lambda: request.app.state.dependencies.monitoring.inspect(device_id, body.mode)[0])


@router.post("/devices/{device_id}/plan")
def plan(device_id: int, body: Mode, request: Request):
    return safe(lambda: request.app.state.dependencies.monitoring.plan(device_id, body.mode))


@router.post("/devices/{device_id}/provision", status_code=202)
@router.post("/devices/{device_id}/reconcile", status_code=202)
def apply(device_id: int, body: Apply, request: Request, background: BackgroundTasks):
    service = request.app.state.dependencies.monitoring
    job = safe(lambda: service.submit(device_id, body.mode, str(body.plan_id)))
    background.add_task(service.run, job["job_id"], str(body.plan_id))
    return {"job_id": job["job_id"], "status": "PENDING"}


@router.post("/provision/bulk", status_code=202)
def bulk(body: Bulk, request: Request, background: BackgroundTasks):
    service = request.app.state.dependencies.monitoring
    enabled = {d["id"] for d in safe(service.list_devices) if not d.get("error_code")}
    planned = []
    for entry in body.plans:
        approved = safe(lambda: service.store.get_plan(str(entry.plan_id)))
        if approved["device_id"] not in enabled or approved["mode"] != entry.mode:
            raise AtlasError(409, "bulk_plan_invalid", "Every plan must match an enabled device and mode")
        planned.append((approved["device_id"], entry))
    if len({d for d, _ in planned}) != len(planned):
        raise AtlasError(409, "duplicate_bulk_device", "A device may appear only once")
    jobs = []
    for device_id, entry in planned:
        job = safe(lambda: service.submit(device_id, entry.mode, str(entry.plan_id)))
        # FastAPI executes these background callbacks in order; same single-device code.
        background.add_task(service.run, job["job_id"], str(entry.plan_id))
        jobs.append({"device_id": device_id, "job_id": job["job_id"], "status": "PENDING"})
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
def job(job_id: UUID, request: Request):
    return safe(lambda: request.app.state.dependencies.monitoring.job(str(job_id)))
