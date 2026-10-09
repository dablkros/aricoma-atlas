"""Read-only readiness checks for required Atlas dependencies."""

import logging

from fastapi import APIRouter, Request, Response, status

from atlas.api.schemas.readiness import DependencyStatuses, ReadinessResponse


logger = logging.getLogger("atlas.api.readiness")
router = APIRouter(tags=["health"])


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    summary="Check whether required Atlas dependencies are ready",
)
def readiness(request: Request, response: Response) -> ReadinessResponse:
    result = request.app.state.dependencies.platform_status.check()
    dependency_names = ("openbao", "netbox", "oxidized", "zabbix")
    states = {
        name: "ok" if result.components[name].status == "healthy" else "error"
        for name in dependency_names
    }
    reasons = {
        name: result.components[name].reason
        for name in dependency_names
        if result.components[name].reason
    }

    ready = all(value == "ok" for value in states.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    logger.info(
        "dependencies.checked",
        extra={
            "event": "dependencies.checked",
            "dependency_statuses": states,
            "dependency_reasons": reasons,
        },
    )
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        dependencies=DependencyStatuses(**states),
        reasons=reasons,
    )
