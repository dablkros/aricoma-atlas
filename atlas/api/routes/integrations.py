"""Operator-authorized diagnostics for external Atlas integrations."""

from fastapi import APIRouter, Depends, Request

from atlas.api.operator_auth import require_operator
from atlas.api.schemas.integrations import CiscoSupportTestResponse


router = APIRouter(prefix="/integrations", tags=["integrations"])


def integration_authorized(request: Request) -> None:
    require_operator(request, scope="integration")


@router.post(
    "/cisco/test",
    response_model=CiscoSupportTestResponse,
    dependencies=[Depends(integration_authorized)],
    summary="Test Cisco Support API credentials and entitlements",
)
def test_cisco_support(request: Request) -> CiscoSupportTestResponse:
    result = request.app.state.dependencies.cisco_support.test_access()
    return CiscoSupportTestResponse(
        overall=result.overall,
        oauth=result.oauth,
        software_suggestion=result.software_suggestion,
        eox=result.eox,
    )


__all__ = ["router"]
