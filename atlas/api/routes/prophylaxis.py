"""HTTP boundary for explicit Profylaxia checks and local history."""

from fastapi import APIRouter, Query, Request, status

from atlas.api.errors import AtlasError
from atlas.api.schemas.prophylaxis import ProphylaxisDeviceResponse
from atlas.prophylaxis.models import StoredCPUCheckResult
from atlas.prophylaxis.store import ResultStoreError
from atlas.services.errors import DependencyError


router = APIRouter(prefix="/prophylaxis", tags=["prophylaxis"])


def _dependency_error(exc: DependencyError) -> AtlasError:
    code = (
        "netbox_authentication_failed"
        if exc.reason == "authentication_failed"
        else "netbox_unavailable"
    )
    return AtlasError(503, code, "NetBox is unavailable")


@router.get(
    "/devices",
    response_model=list[ProphylaxisDeviceResponse],
    status_code=status.HTTP_200_OK,
    summary="List devices eligible for the CPU utilization check",
)
def prophylaxis_devices(request: Request) -> list[ProphylaxisDeviceResponse]:
    try:
        devices = request.app.state.dependencies.prophylaxis.list_devices()
    except DependencyError as exc:
        raise _dependency_error(exc) from None
    return [
        ProphylaxisDeviceResponse(
            id=item.id,
            name=item.name,
            ip=item.ip,
            platform=item.platform,
        )
        for item in devices
    ]


@router.post(
    "/devices/{device_id}/checks/cpu",
    response_model=StoredCPUCheckResult,
    status_code=status.HTTP_200_OK,
    summary="Run and persist one CPU utilization check",
)
def run_cpu_check(device_id: int, request: Request) -> StoredCPUCheckResult:
    try:
        return request.app.state.dependencies.prophylaxis.run_cpu_check(device_id)
    except ResultStoreError:
        raise AtlasError(
            500,
            "result_store_unavailable",
            "The Profylaxia result could not be stored",
        ) from None


@router.get(
    "/results",
    response_model=list[StoredCPUCheckResult],
    status_code=status.HTTP_200_OK,
    summary="List recent locally stored Profylaxia results",
)
def prophylaxis_results(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[StoredCPUCheckResult]:
    try:
        return request.app.state.dependencies.prophylaxis.result_history(limit)
    except ResultStoreError:
        raise AtlasError(
            500,
            "result_store_unavailable",
            "The Profylaxia result history is unavailable",
        ) from None


__all__ = ["router"]
