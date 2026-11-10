"""Unprivileged Ansible executor sharing only the VPN worker network namespace."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas.logging import configure_logging
from atlas.prophylaxis.ansible_backend import AnsibleBackend
from atlas.prophylaxis.errors import ProphylaxisExecutionError
from atlas.prophylaxis.models import CheckId, CollectorId
from atlas.prophylaxis.platforms import AnsiblePlatformRegistry, FORTIOS_SSH_PROFILE


configure_logging(os.environ.get("ATLAS_LOG_LEVEL", "INFO"))
logger = logging.getLogger("atlas.vpn.collector")


class CollectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    platform: str = Field(min_length=1, max_length=100)
    transport: str = Field(pattern="^(ssh|https)$")
    collector_id: CollectorId
    check_id: CheckId
    credentials: dict
    telnet_fallback_port: int | None = Field(default=None, ge=1, le=65535)
    device_model: str | None = Field(default=None, max_length=255)
    raw_mode: str | None = Field(default=None, pattern="^(identity|full_collection)$")


def _token() -> str:
    path = Path(os.environ.get(
        "ATLAS_VPN_WORKER_TOKEN_FILE", "/run/secrets/atlas-vpn-worker-token"
    ))
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(503, "collector_identity_unavailable") from None
    if not 32 <= len(value) <= 256:
        raise HTTPException(503, "collector_identity_unavailable")
    return value


def _authorized(value: str | None) -> None:
    if value is None or not hmac.compare_digest(value, f"Bearer {_token()}"):
        raise HTTPException(401, "collector_authentication_failed")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
registry = AnsiblePlatformRegistry()
backend = AnsibleBackend(
    Path(os.environ.get("ATLAS_ANSIBLE_PROJECT_DIR", "/app/automation")),
    known_hosts_file=Path(os.environ.get(
        "ATLAS_SSH_KNOWN_HOSTS_FILE", "/run/atlas/ssh/known_hosts"
    )),
    strict_host_keys=os.environ.get("ATLAS_SSH_STRICT_HOST_KEYS", "false").lower() == "true",
    connect_timeout=int(os.environ.get("ATLAS_SSH_CONNECT_TIMEOUT", "10")),
    command_timeout=int(os.environ.get("ATLAS_SSH_COMMAND_TIMEOUT", "120")),
    job_timeout=int(os.environ.get("ATLAS_ANSIBLE_JOB_TIMEOUT", "300")),
    fortios_validate_certs=os.environ.get("ATLAS_FORTIOS_VALIDATE_CERTS", "false").lower() == "true",
)


@app.exception_handler(ProphylaxisExecutionError)
async def execution_error_handler(
    _request: Request,
    exc: ProphylaxisExecutionError,
) -> JSONResponse:
    logger.warning(
        "vpn.collector_execution_failed",
        extra={
            "event": "vpn.collector_execution_failed",
            "reason_code": exc.code,
            "exception_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "code": exc.code,
                "message": "The VPN collector could not execute the requested check",
            }
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/execute")
def execute(body: CollectorRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorized(authorization)
    profile = registry.resolve(body.platform)
    if body.platform == "fortios" and body.transport == "ssh":
        profile = FORTIOS_SSH_PROFILE
    elif profile.transport != body.transport:
        raise HTTPException(422, "collector_transport_mismatch")
    result = backend.execute(
        host=body.host, port=body.port, profile=profile,
        collector_id=body.collector_id, check_id=body.check_id,
        credentials=body.credentials,
        telnet_fallback_port=body.telnet_fallback_port,
        device_model=body.device_model,
        raw_mode=body.raw_mode,
    )
    return result.model_dump(mode="json")


__all__ = ["app"]
