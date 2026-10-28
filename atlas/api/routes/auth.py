"""Atlas administrator login, session inspection and logout."""

import logging

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from atlas.api.errors import AtlasError
from atlas.api.operator_auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    authenticated_principal,
)
from atlas.auth import AuthenticationError, AuthenticationUnavailable


router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger("atlas.auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def _session_token(request: Request) -> str:
    supplied = request.headers.get("Authorization", "")
    if supplied.startswith("Bearer "):
        return supplied[7:]
    return request.cookies.get(SESSION_COOKIE, "")


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    try:
        token, csrf_token, principal, ttl = request.app.state.auth.login(
            body.username, body.password
        )
    except AuthenticationError:
        logger.warning(
            "authentication.login_failed",
            extra={"event": "authentication.login_failed", "username": body.username[:64]},
        )
        raise AtlasError(401, "invalid_credentials", "Invalid username or password") from None
    except AuthenticationUnavailable:
        raise AtlasError(
            503,
            "authentication_unavailable",
            "Administrator authentication is temporarily unavailable",
        ) from None
    secure = (
        request.url.scheme == "https"
        or request.headers.get("X-Forwarded-Proto", "").casefold() == "https"
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=ttl,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    logger.info(
        "authentication.login_succeeded",
        extra={
            "event": "authentication.login_succeeded",
            "username": principal.username,
            "role": principal.role,
        },
    )
    request.state.principal = principal
    result = {
        "username": principal.username,
        "role": principal.role,
        "expires_in": ttl,
    }
    if request.headers.get("X-Atlas-CLI") == "1":
        # CLI clients cannot consume the browser-only HttpOnly cookie.
        result.update({"access_token": token, "token_type": "Bearer"})
    return result


@router.get("/me")
def me(request: Request) -> dict:
    principal = authenticated_principal(request, enforce_csrf=False)
    if principal is None:
        raise AtlasError(401, "authentication_required", "Administrator login required")
    return {"username": principal.username, "role": principal.role}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    principal = authenticated_principal(request)
    if principal is not None:
        request.app.state.auth.logout(_session_token(request))
        logger.info(
            "authentication.logout",
            extra={
                "event": "authentication.logout",
                "username": principal.username,
                "role": principal.role,
            },
        )
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


__all__ = ["router"]
