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
    states = {}
    for name in dependency_names:
        component_status = result.components[name].status
        states[name] = (
            "disabled" if component_status == "disabled"
            else "ok" if component_status == "healthy"
            else "error"
        )
    reasons = {
        name: result.components[name].reason
        for name in dependency_names
        if result.components[name].reason
    }

    ready = all(value in {"ok", "disabled"} for value in states.values())
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
