"""Human-session and machine-token authorization for privileged operations."""

from pathlib import Path
import secrets

from fastapi import Request

from atlas.api.errors import AtlasError
from atlas.auth import Principal


SESSION_COOKIE = "atlas_admin_session"
CSRF_COOKIE = "atlas_csrf"


def _supplied_bearer(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    if value.startswith("Bearer "):
        return value[7:]
    return ""


def authenticated_principal(request: Request, *, enforce_csrf: bool = True) -> Principal | None:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        return None
    bearer = _supplied_bearer(request)
    cookie = request.cookies.get(SESSION_COOKIE, "")
    token = bearer or cookie
    authenticated = service.authenticate(token)
    if authenticated is None:
        return None
    principal, csrf_token = authenticated
    if enforce_csrf and cookie and not bearer and request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied_csrf = request.headers.get("X-CSRF-Token", "")
        cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
        if (
            not supplied_csrf
            or not cookie_csrf
            or not secrets.compare_digest(supplied_csrf, csrf_token)
            or not secrets.compare_digest(cookie_csrf, csrf_token)
        ):
            raise AtlasError(403, "csrf_validation_failed", "Request validation failed")
    request.state.principal = principal
    return principal


def validated_operator_token(request: Request, *, scope: str) -> str:
    principal = authenticated_principal(request)
    if principal is not None:
        # Existing plan hashes need a stable secret for the lifetime of a session.
        return _supplied_bearer(request) or request.cookies.get(SESSION_COOKIE, "")
    try:
        token = Path(
            request.app.state.settings.monitoring_operator_token_file
        ).read_text(encoding="utf-8").strip()
    except OSError:
        raise AtlasError(
            503,
            f"{scope}_authorization_not_configured",
            f"{scope.replace('_', ' ').title()} operator access is not configured",
        ) from None
    supplied = request.headers.get("Authorization", "")
    if len(token) < 32 or not secrets.compare_digest(supplied, f"Bearer {token}"):
        raise AtlasError(
            401,
            f"{scope}_unauthorized",
            f"{scope.replace('_', ' ').title()} operator authorization required",
        )
    request.state.principal = Principal(
        username="automation-token",
        role="automation",
        authentication="shared_token",
    )
    return token


def require_operator(request: Request, *, scope: str) -> None:
    validated_operator_token(request, scope=scope)


__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "authenticated_principal",
    "require_operator",
    "validated_operator_token",
]
