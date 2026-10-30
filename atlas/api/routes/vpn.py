"""Authorized REST API for the persistent VPN connection registry."""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Query, Request, status

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import require_operator, validated_operator_token
from atlas.vpn.models import (
    VPNAssignmentRequest,
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


def _assignment_hash(body: VPNAssignmentRequest, plan: dict, token: str) -> str:
    canonical = json.dumps(
        {
            "request": body.model_dump(exclude={"plan_hash"}, mode="json"),
            "plan": plan,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hmac.new(token.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


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


@router.get("/assignments/options")
def assignment_options(
    request: Request,
    tenant_id: int = Query(gt=0),
) -> dict:
    validated_operator_token(request, scope="vpn")
    return _translate(
        lambda: request.app.state.dependencies.vpn.assignment_options(tenant_id)
    )


@router.post("/assignments/plan")
def plan_assignment(body: VPNAssignmentRequest, request: Request) -> dict:
    token = validated_operator_token(request, scope="vpn")
    plan = _translate(
        lambda: request.app.state.dependencies.vpn.plan_assignment(
            tenant_id=body.tenant_id,
            connection_id=body.connection_id,
            target_type=body.target_type,
            target_ids=body.target_ids,
        )
    )
    plan["plan_hash"] = _assignment_hash(body, plan, token)
    logger.info(
        "vpn.assignment_planned",
        extra={
            "event": "vpn.assignment_planned",
            "tenant_id": body.tenant_id,
            "connection_id": body.connection_id,
            "target_type": body.target_type,
            "target_count": len(body.target_ids),
        },
    )
    return plan


@router.post("/assignments/apply")
def apply_assignment(body: VPNAssignmentRequest, request: Request) -> dict:
    token = validated_operator_token(request, scope="vpn")
    preview = _translate(
        lambda: request.app.state.dependencies.vpn.plan_assignment(
            tenant_id=body.tenant_id,
            connection_id=body.connection_id,
            target_type=body.target_type,
            target_ids=body.target_ids,
        )
    )
    expected = _assignment_hash(body, preview, token)
    if body.plan_hash is None or not hmac.compare_digest(body.plan_hash, expected):
        raise AtlasError(
            409,
            "vpn_assignment_plan_stale",
            "VPN assignment or dependency state changed; create a new plan",
        )
    result = _translate(
        lambda: request.app.state.dependencies.vpn.apply_assignment(
            expected_plan=preview,
            tenant_id=body.tenant_id,
            connection_id=body.connection_id,
            target_type=body.target_type,
            target_ids=body.target_ids,
        )
    )
    result["plan_hash"] = expected
    logger.info(
        "vpn.assignment_applied",
        extra={
            "event": "vpn.assignment_applied",
            "tenant_id": body.tenant_id,
            "connection_id": body.connection_id,
            "target_type": body.target_type,
            "target_count": len(body.target_ids),
        },
    )
    return result


__all__ = ["router"]
