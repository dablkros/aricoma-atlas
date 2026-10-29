"""Authorized plan/apply API for device credential onboarding."""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Request

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import validated_operator_token
from atlas.api.schemas.credentials import CredentialOnboardingRequest
from atlas.services.credential_onboarding import CredentialOnboardingInvalid
from atlas.services.errors import DependencyError


router = APIRouter(prefix="/credentials", tags=["credentials"])
logger = logging.getLogger("atlas.credentials")


def _authorize(request: Request) -> str:
    return validated_operator_token(request, scope="credential_onboarding")


def _plan_hash(
    body: CredentialOnboardingRequest,
    plan: dict,
    operator_token: str,
) -> str:
    canonical = json.dumps(
        {
            "request": body.model_dump(exclude={"plan_hash"}, mode="json"),
            "plan": plan,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hmac.new(
        operator_token.encode("utf-8"), canonical, hashlib.sha256
    ).hexdigest()


def _translate(operation):
    try:
        return operation()
    except CredentialOnboardingInvalid as exc:
        raise AtlasError(422, "credential_onboarding_invalid", str(exc)) from None
    except DependencyError:
        raise AtlasError(
            503,
            "credential_onboarding_dependency_unavailable",
            "Credential onboarding dependency is unavailable",
        ) from None


@router.get("/options")
def options(request: Request) -> dict:
    _authorize(request)
    return _translate(request.app.state.dependencies.credentials.options)


@router.post("/plan")
def plan(body: CredentialOnboardingRequest, request: Request) -> dict:
    token = _authorize(request)
    logger.info(
        "credential_onboarding.operation_started",
        extra={"event": "credential_onboarding.operation_started", "operation": "plan",
               "device_ids": body.device_ids, "device_count": len(body.device_ids)},
    )
    result = _translate(lambda: request.app.state.dependencies.credentials.plan(body))
    result["plan_hash"] = _plan_hash(body, result, token)
    logger.info(
        "credential_onboarding.operation_completed",
        extra={"event": "credential_onboarding.operation_completed", "operation": "plan",
               "device_ids": body.device_ids, "device_count": len(body.device_ids),
               "apply_allowed": result["apply_allowed"]},
    )
    return result


@router.post("/apply")
def apply(body: CredentialOnboardingRequest, request: Request) -> dict:
    token = _authorize(request)
    preview = _translate(lambda: request.app.state.dependencies.credentials.plan(body))
    expected = _plan_hash(body, preview, token)
    if body.plan_hash is None or not hmac.compare_digest(body.plan_hash, expected):
        raise AtlasError(
            409,
            "credential_onboarding_plan_stale",
            "Credential input or dependency state changed; create a new plan",
        )
    if not preview["apply_allowed"]:
        raise AtlasError(
            409,
            "credential_onboarding_plan_blocked",
            "Resolve all credential conflicts before apply",
        )
    logger.info(
        "credential_onboarding.operation_started",
        extra={"event": "credential_onboarding.operation_started", "operation": "apply",
               "device_ids": body.device_ids, "device_count": len(body.device_ids)},
    )
    result = _translate(
        lambda: request.app.state.dependencies.credentials.apply(
            body,
            expected_plan=preview,
        )
    )
    logger.info(
        "credential_onboarding.operation_completed",
        extra={"event": "credential_onboarding.operation_completed", "operation": "apply",
               "device_ids": body.device_ids, "device_count": len(body.device_ids)},
    )
    return result


__all__ = ["router"]
