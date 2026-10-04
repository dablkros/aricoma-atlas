"""HTTP boundary for Atlas-owned Oxidized orchestration."""

from fastapi import APIRouter, Request, Response, status

from atlas.api.errors import AtlasError
from atlas.api.schemas.oxidized import (
    OxidizedBackupResponse,
    OxidizedDeviceResponse,
    OxidizedStatusResponse,
    OxidizedSyncResponse,
)
from atlas.services.oxidized_operations import OxidizedOperationFailure
from atlas.services.oxidized_sync import OxidizedSyncFailure

router = APIRouter(prefix="/oxidized", tags=["oxidized"])


_FAILURE_MESSAGES = {
    "sync_already_running": "Oxidized inventory synchronization is already running",
    "netbox_unavailable": "NetBox is unavailable",
    "netbox_authentication_failed": "NetBox authentication failed",
    "openbao_unavailable": "OpenBao is unavailable",
    "openbao_authentication_failed": "OpenBao authentication failed",
    "inventory_invalid": "Current Oxidized inventory is invalid",
    "inventory_write_failed": "Oxidized inventory could not be written",
    "invalid_device_name": "The Oxidized device name is invalid",
    "device_not_found": "The device is not present in the runtime inventory",
    "oxidized_unavailable": "Oxidized is currently unavailable",
    "oxidized_authentication_failed": "Oxidized authentication failed",
}


def _operation_error(exc: OxidizedOperationFailure) -> AtlasError:
    return AtlasError(
        exc.status_code,
        exc.code,
        _FAILURE_MESSAGES.get(exc.code, "Oxidized operation failed"),
    )


@router.get(
    "/status",
    response_model=OxidizedStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the Oxidized inventory overview",
)
def oxidized_status(request: Request) -> OxidizedStatusResponse:
    try:
        result = request.app.state.dependencies.oxidized_operations.status()
    except OxidizedOperationFailure as exc:
        raise _operation_error(exc) from None
    return OxidizedStatusResponse(
        status=result.status,
        netbox_enabled_devices=result.netbox_enabled_devices,
        runtime_inventory_devices=result.runtime_inventory_devices,
        inventory_issues=result.inventory_issues,
    )


@router.get(
    "/devices",
    response_model=list[OxidizedDeviceResponse],
    status_code=status.HTTP_200_OK,
    summary="List safe runtime Oxidized devices",
)
def oxidized_devices(request: Request) -> list[OxidizedDeviceResponse]:
    try:
        devices = request.app.state.dependencies.oxidized_operations.list_devices()
    except OxidizedOperationFailure as exc:
        raise _operation_error(exc) from None
    return [
        OxidizedDeviceResponse(name=item.name, ip=item.ip, model=item.model)
        for item in devices
    ]


@router.post(
    "/devices/{device}/backup",
    response_model=OxidizedBackupResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an Oxidized backup for one runtime device",
)
def queue_device_backup(device: str, request: Request) -> OxidizedBackupResponse:
    try:
        selected = request.app.state.dependencies.oxidized_operations.queue_backup(
            device
        )
    except OxidizedOperationFailure as exc:
        raise _operation_error(exc) from None
    return OxidizedBackupResponse(status="accepted", device=selected.name)


@router.post(
    "/sync",
    response_model=OxidizedSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Synchronize the Oxidized runtime inventory",
)
def sync_oxidized(request: Request, response: Response) -> OxidizedSyncResponse:
    try:
        result = request.app.state.dependencies.oxidized_sync.sync()
    except OxidizedSyncFailure as exc:
        raise AtlasError(
            exc.status_code,
            exc.code,
            _FAILURE_MESSAGES.get(exc.code, "Oxidized synchronization failed"),
        ) from None

    if result.status == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return OxidizedSyncResponse(
        status=result.status,
        inventory_changed=result.inventory_changed,
        inventory_updated=result.inventory_updated,
        oxidized_reloaded=result.oxidized_reloaded,
        summary={
            "total": result.summary.total,
            "added": result.summary.added,
            "updated": result.summary.updated,
            "removed": result.summary.removed,
            "unchanged": result.summary.unchanged,
            "skipped": result.summary.skipped,
            "errors": result.summary.errors,
        },
        added=list(result.added),
        updated=list(result.updated),
        removed=list(result.removed),
        issues=[
            {"device": issue.device, "code": issue.code}
            for issue in result.issues
        ],
    )
