"""Read-only readiness checks for required Atlas dependencies."""

import logging

from fastapi import APIRouter, Request, Response, status

from atlas.api.schemas.readiness import DependencyStatuses, ReadinessResponse
from atlas.services.errors import DependencyError


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
    services = request.app.state.dependencies
    states = {}
    reasons = {}

    for name in ("openbao", "netbox", "oxidized"):
        try:
            getattr(services, name).check_ready()
            states[name] = "ok"
        except DependencyError as exc:
            states[name] = "error"
            reasons[name] = exc.reason
        except Exception:
            states[name] = "error"
            reasons[name] = "unavailable"

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
