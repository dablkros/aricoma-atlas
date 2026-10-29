"""Authorized REST API for the persistent VPN connection registry."""

import logging

from fastapi import APIRouter, Depends, Query, Request, status

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator
from atlas.vpn.models import (
    VPNConnectionCreate,
    VPNConnectionPage,
    VPNConnectionPatch,
    VPNConnectionRecord,
    VPNResolutionResult,
    VPNValidationResult,
)
from atlas.vpn.service import VPNRegistryError


router = APIRouter(prefix="/v1/vpn", tags=["vpn"])
logger = logging.getLogger("atlas.vpn")


def authorized(request: Request) -> None:
    require_operator(request, scope="vpn")


def _translate(operation):
    try:
        return operation()
    except VPNRegistryError as exc:
        messages = {
            "connection_not_found": "VPN connection was not found",
            "connection_id_conflict": "VPN connection ID already exists",
            "vpn_registry_unavailable": "VPN registry is unavailable",
            "netbox_unavailable": "NetBox is unavailable",
            "vpn_secret_not_found": "VPN secret reference was not found",
            "vpn_secret_validation_unavailable": "VPN secret validation is unavailable",
        }
        raise AtlasError(
            exc.status_code,
            exc.code,
            messages.get(exc.code, "VPN connection validation failed"),
        ) from None


@router.get(
    "/connections",
    response_model=VPNConnectionPage,
    dependencies=[Depends(authorized)],
)
def list_connections(
    request: Request,
    tenant_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> VPNConnectionPage:
    return _translate(
        lambda: request.app.state.dependencies.vpn.list(
            tenant_id=tenant_id, limit=limit, offset=offset,
        )
    )


@router.post(
    "/connections",
    response_model=VPNConnectionRecord,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(authorized)],
)
def create_connection(
    body: VPNConnectionCreate, request: Request,
) -> VPNConnectionRecord:
    result = _translate(lambda: request.app.state.dependencies.vpn.create(body))
    logger.info(
        "vpn.connection_created",
        extra={"event": "vpn.connection_created", "connection_id": result.connection_id,
               "tenant_id": result.tenant_id},
    )
    return result


@router.get(
    "/connections/{connection_id}",
    response_model=VPNConnectionRecord,
    dependencies=[Depends(authorized)],
)
def get_connection(connection_id: str, request: Request) -> VPNConnectionRecord:
    return _translate(lambda: request.app.state.dependencies.vpn.get(connection_id))


@router.patch(
    "/connections/{connection_id}",
    response_model=VPNConnectionRecord,
    dependencies=[Depends(authorized)],
)
def patch_connection(
    connection_id: str, body: VPNConnectionPatch, request: Request,
) -> VPNConnectionRecord:
    result = _translate(
        lambda: request.app.state.dependencies.vpn.patch(connection_id, body)
    )
    logger.info(
        "vpn.connection_updated",
        extra={"event": "vpn.connection_updated", "connection_id": connection_id,
               "tenant_id": result.tenant_id},
    )
    return result


@router.delete(
    "/connections/{connection_id}",
    response_model=VPNConnectionRecord,
    dependencies=[Depends(authorized)],
)
def deactivate_connection(
    connection_id: str, request: Request,
) -> VPNConnectionRecord:
    result = _translate(
        lambda: request.app.state.dependencies.vpn.deactivate(connection_id)
    )
    logger.info(
        "vpn.connection_disabled",
        extra={"event": "vpn.connection_disabled", "connection_id": connection_id,
               "tenant_id": result.tenant_id},
    )
    return result


@router.post(
    "/connections/{connection_id}/validate",
    response_model=VPNValidationResult,
    dependencies=[Depends(authorized)],
)
def validate_connection(connection_id: str, request: Request) -> dict:
    return _translate(
        lambda: request.app.state.dependencies.vpn.validate(connection_id)
    )


@router.get(
    "/devices/{device_id}/resolve",
    response_model=VPNResolutionResult,
    dependencies=[Depends(authorized)],
)
def resolve_device(
    device_id: int,
    request: Request,
    tenant_id: int | None = Query(default=None, gt=0),
) -> dict:
    return _translate(
        lambda: request.app.state.dependencies.vpn.resolve_device(
            device_id, expected_tenant=tenant_id,
        )
    )


__all__ = ["router"]
