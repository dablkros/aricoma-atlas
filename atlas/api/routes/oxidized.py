"""HTTP boundary for Atlas-owned Oxidized orchestration."""

from fastapi import APIRouter, Request, Response, status

from atlas.api.errors import AtlasError
from atlas.api.schemas.oxidized import OxidizedSyncResponse
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
}


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
