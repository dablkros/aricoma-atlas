"""Shared bearer-token authorization for privileged Atlas operations."""

from pathlib import Path
import secrets

from fastapi import Request

from atlas.api.errors import AtlasError


def validated_operator_token(request: Request, *, scope: str) -> str:
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
    return token


def require_operator(request: Request, *, scope: str) -> None:
    validated_operator_token(request, scope=scope)


__all__ = ["require_operator", "validated_operator_token"]
