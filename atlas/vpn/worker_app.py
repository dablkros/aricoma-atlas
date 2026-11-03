"""Privileged, single-operation VPN worker with mandatory teardown."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from atlas.logging import configure_logging
from atlas.openbao_client import OpenBaoClient
from atlas.services.openbao import OpenBaoService
from atlas.vpn.forticlient_profile import (
    FortiClientProfile, FortiClientProfileError, FortiClientSSLProfile,
)
from atlas.vpn.openvpn_profile import (
    OpenVPNProfileError, parse_openvpn_profile, render_openvpn_runtime_config,
)
from atlas.vpn.strongswan_config import (
    VPNSecrets,
    render_fortinet_daemon_fragment,
    render_swanctl,
)


configure_logging(os.environ.get("ATLAS_LOG_LEVEL", "INFO"))
logger = logging.getLogger("atlas.vpn.worker")
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


class WorkerError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class WorkerTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    tenant_id: int = Field(gt=0)
    secret_ref: str = Field(pattern=r"^vpn/connections/[a-z0-9]+(?:-[a-z0-9]+)*$")
    gateway_host: str = Field(min_length=1, max_length=253)
    gateway_port: int = Field(ge=1, le=65535)
    driver: str
    protocol: str
    external_mfa: bool = False
    mfa_mode: Literal["none", "auto", "push", "otp"] = "none"
    target_ip: str
    target_port: int = Field(ge=1, le=65535)

    @field_validator("target_ip")
    @classmethod
    def ipv4_only(cls, value: str) -> str:
        try:
            socket.inet_pton(socket.AF_INET, value)
        except OSError:
            raise ValueError("target_ip must be IPv4") from None
        return value

    def model_post_init(self, _context) -> None:
        if self.secret_ref != f"vpn/connections/{self.connection_id}":
            raise ValueError("secret_ref does not match connection_id")

    @property
    def effective_mfa_mode(self) -> str:
        if self.mfa_mode != "none":
            return self.mfa_mode
        return "auto" if self.external_mfa else "none"


class WorkerSessionOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    tenant_id: int = Field(gt=0)
    secret_ref: str = Field(pattern=r"^vpn/connections/[a-z0-9]+(?:-[a-z0-9]+)*$")
    gateway_host: str = Field(min_length=1, max_length=253)
    gateway_port: int = Field(ge=1, le=65535)
    driver: str
    protocol: str
    external_mfa: bool = False
    mfa_mode: Literal["none", "auto", "push", "otp"] = "none"
    target_ips: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("target_ips")
    @classmethod
    def ipv4_targets_only(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        for value in values:
            try:
                socket.inet_pton(socket.AF_INET, value)
            except OSError:
                raise ValueError("target_ips must contain IPv4 addresses") from None
            if value not in unique:
                unique.append(value)
        return unique

    def model_post_init(self, _context) -> None:
        if self.secret_ref != f"vpn/connections/{self.connection_id}":
            raise ValueError("secret_ref does not match connection_id")

    @property
    def effective_mfa_mode(self) -> str:
        if self.mfa_mode != "none":
            return self.mfa_mode
        return "auto" if self.external_mfa else "none"


class WorkerOTPRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    code: SecretStr = Field(repr=False)

    @field_validator("code")
    @classmethod
    def numeric_code(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[0-9]{4,16}", value.get_secret_value()):
            raise ValueError("code must contain 4 to 16 digits")
        return value


@dataclass
class WorkerSession:
    session_id: str
    connection_id: str
    process: subprocess.Popen
    interface: str
    assigned_address: str
    directory: Path
    timer: threading.Timer | None = None


@dataclass
class WorkerMFAOperation:
    operation_id: str
    request_id: str
    body: WorkerTestRequest
    state: str = "connecting"
    result: dict | None = None
    otp_response_file: Path | None = None


class WorkerSettings:
    def __init__(self) -> None:
        self.token_file = Path(os.environ.get(
            "ATLAS_VPN_WORKER_TOKEN_FILE", "/run/secrets/atlas-vpn-worker-token"
        ))
        self.identity_file = Path(os.environ.get(
            "ATLAS_VPN_WORKER_IDENTITY_FILE", "/run/secrets/atlas-vpn-worker.json"
        ))
        self.openbao_url = os.environ.get("ATLAS_OPENBAO_URL", "http://atlas-openbao:8200")
        self.swanctl = os.environ.get("ATLAS_SWANCTL_BIN", "/usr/local/sbin/swanctl")
        self.charon = os.environ.get("ATLAS_CHARON_BIN", "/usr/local/sbin/charon-systemd")
        self.runtime = Path(os.environ.get("ATLAS_VPN_WORKER_RUNTIME", "/run/atlas-vpn-worker"))
        self.connect_timeout = int(os.environ.get("ATLAS_VPN_CONNECT_TIMEOUT", "45"))
        self.mfa_timeout = int(os.environ.get("ATLAS_VPN_MFA_TIMEOUT", "120"))
        self.target_timeout = float(os.environ.get("ATLAS_VPN_TARGET_TIMEOUT", "5"))
        self.session_timeout = int(os.environ.get("ATLAS_VPN_SESSION_TIMEOUT", "3600"))
        self.openfortivpn = os.environ.get("ATLAS_OPENFORTIVPN_BIN", "/usr/bin/openfortivpn")
        self.openvpn = os.environ.get("ATLAS_OPENVPN_BIN", "/usr/sbin/openvpn")


settings = WorkerSettings()
worker_lock = threading.Lock()
session_guard = threading.Lock()
active_session: WorkerSession | None = None
mfa_guard = threading.Lock()
mfa_operations: dict[str, WorkerMFAOperation] = {}
app = FastAPI(title="Atlas VPN worker", docs_url=None, redoc_url=None, openapi_url=None)


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "")
    return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = _request_id(request)
    logger.warning(
        "vpn.worker_request_rejected",
        extra={
            "event": "vpn.worker_request_rejected",
            "request_id": request_id,
            "reason_code": code,
            "status_code": status_code,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(WorkerError)
async def worker_error_handler(request: Request, exc: WorkerError) -> JSONResponse:
    return _error_response(request, exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def worker_validation_error_handler(
    request: Request, _exc: RequestValidationError,
) -> JSONResponse:
    return _error_response(
        request,
        422,
        "vpn_worker_request_rejected",
        "The VPN worker rejected the test request",
    )


def _token() -> str:
    try:
        value = settings.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise WorkerError(
            503,
            "vpn_worker_identity_unavailable",
            "The VPN worker identity is unavailable",
        ) from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value):
        raise WorkerError(
            503,
            "vpn_worker_identity_unavailable",
            "The VPN worker identity is unavailable",
        )
    return value


def _authorized(authorization: str | None) -> None:
    import hmac
    expected = f"Bearer {_token()}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise WorkerError(
            401,
            "vpn_worker_authentication_failed",
            "Atlas could not authenticate to the VPN worker",
        )


def _run(arguments: list[str], *, timeout: float = 10, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, check=False, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("worker_command_failed") from None
    if check and result.returncode != 0:
        raise RuntimeError("worker_command_failed")
    return result


def _profile(secret: dict, body: WorkerTestRequest) -> tuple[FortiClientProfile, VPNSecrets]:
    if (secret.get("schema_version") != 1
            or secret.get("profile_type", "ipsec-ikev2") != "ipsec-ikev2"
            or secret.get("connection_id") != body.connection_id
            or secret.get("tenant_id") != body.tenant_id):
        raise RuntimeError("secret_schema_invalid")
    raw = secret.get("profile")
    if not isinstance(raw, dict):
        raise RuntimeError("secret_schema_invalid")
    try:
        profile = FortiClientProfile(
            name=raw["name"], gateway=raw["gateway"], network_id=raw["network_id"],
            ike_version=raw["ike_version"], gateway_port=raw["gateway_port"],
            local_id=raw.get("local_id"), user_auth_enabled=raw["user_auth_enabled"],
            phase1_proposals=tuple(raw["phase1_proposals"]), phase1_dh_group=raw["phase1_dh_group"],
            phase2_proposals=tuple(raw["phase2_proposals"]), phase2_dh_group=raw["phase2_dh_group"],
            pfs=raw["pfs"], encrypted_auth_info="",
        )
        psk = base64.b64decode(secret["psk_base64"], validate=True)
        credentials = VPNSecrets(secret["username"], secret["password"], psk)
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("secret_schema_invalid") from None
    if profile.gateway != body.gateway_host or profile.gateway_port != body.gateway_port:
        raise RuntimeError("profile_registry_mismatch")
    return profile, credentials


def _config_value(value, name: str, *, maximum: int = 512) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or any(c in value for c in "\r\n\0")):
        raise RuntimeError(f"invalid_{name}")
    return value


def _ssl_profile(secret: dict, body: WorkerTestRequest) -> tuple[FortiClientSSLProfile, str, str]:
    if (secret.get("schema_version") != 1 or secret.get("profile_type") != "ssl-vpn"
            or secret.get("connection_id") != body.connection_id
            or secret.get("tenant_id") != body.tenant_id):
        raise RuntimeError("secret_schema_invalid")
    raw = secret.get("profile")
    if not isinstance(raw, dict):
        raise RuntimeError("secret_schema_invalid")
    try:
        profile = FortiClientSSLProfile(
            name=_config_value(raw["name"], "name"),
            gateway=_config_value(raw["gateway"], "gateway", maximum=253),
            gateway_port=int(raw["gateway_port"]),
            realm=(_config_value(raw["realm"], "realm") if raw.get("realm") else None),
            sso_enabled=raw["sso_enabled"],
        )
        username = _config_value(secret["username"], "username")
        password = _config_value(secret["password"], "password", maximum=1024)
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("secret_schema_invalid") from None
    if (not isinstance(profile.sso_enabled, bool) or profile.sso_enabled
            or not 1 <= profile.gateway_port <= 65535):
        raise RuntimeError("secret_schema_invalid")
    if profile.gateway != body.gateway_host or profile.gateway_port != body.gateway_port:
        raise RuntimeError("profile_registry_mismatch")
    return profile, username, password


def _openvpn_profile(
    secret: dict, body: WorkerTestRequest | WorkerSessionOpenRequest,
) -> tuple[str, str, str]:
    if (secret.get("schema_version") != 1 or secret.get("profile_type") != "openvpn"
            or secret.get("connection_id") != body.connection_id
            or secret.get("tenant_id") != body.tenant_id):
        raise RuntimeError("secret_schema_invalid")
    try:
        config = secret["config"]
        if (not isinstance(config, str) or not config
                or len(config) > 2 * 1024 * 1024 or "\x00" in config):
            raise ValueError()
        username = _config_value(secret["username"], "username")
        password = _config_value(secret["password"], "password", maximum=1024)
        profile = parse_openvpn_profile(config)
    except (KeyError, TypeError, ValueError, OpenVPNProfileError):
        raise RuntimeError("secret_schema_invalid") from None
    if profile.gateway != body.gateway_host or profile.gateway_port != body.gateway_port:
        raise RuntimeError("profile_registry_mismatch")
    return profile.canonical_config, username, password


def _render_ssl_config(profile: FortiClientSSLProfile, username: str, password: str) -> str:
    lines = [
        f"host = {profile.gateway}", f"port = {profile.gateway_port}",
        f"username = {username}", f"password = {password}",
        "set-dns = 0", "pppd-use-peerdns = 0",
    ]
    if profile.realm:
        lines.append(f"realm = {profile.realm}")
    return "\n".join(lines) + "\n"


def _ssl_links() -> set[str]:
    result = _run(["ip", "-o", "link", "show"], timeout=3, check=False)
    return {match.group(1) for match in re.finditer(r"^\d+:\s+([^:@]+)", result.stdout, re.MULTILINE)}


def _ssl_address(previous_links: set[str]) -> tuple[str | None, str | None]:
    result = _run(["ip", "-4", "-o", "addr", "show"], timeout=3, check=False)
    for match in re.finditer(r"^\d+:\s+([^\s:@]+).*?\binet\s+([0-9.]+)/", result.stdout, re.MULTILINE):
        interface, address = match.groups()
        if interface not in previous_links:
            return interface, address
    return None, None


def _sas() -> tuple[bool, bool, str | None]:
    result = _run([settings.swanctl, "--list-sas", "--raw"], timeout=5, check=False)
    text = result.stdout[:65536]
    ike = "state=ESTABLISHED" in text
    child = "state=INSTALLED" in text
    match = re.search(r"local-ts=\[([0-9.]+)/32\]", text)
    return ike, child, match.group(1) if match else None


def _cleanup(charon: subprocess.Popen | None) -> bool:
    clean = True
    try:
        _run([settings.swanctl, "--terminate", "--ike", "forti-full"], timeout=8, check=False)
    except RuntimeError:
        clean = False
    if charon is not None and charon.poll() is None:
        charon.terminate()
        try:
            charon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            charon.kill()
            charon.wait(timeout=3)
    for command in (["ip", "xfrm", "state", "flush"], ["ip", "xfrm", "policy", "flush"]):
        try:
            if _run(command, timeout=5, check=False).returncode != 0:
                clean = False
        except RuntimeError:
            clean = False
    return clean


def _cleanup_ssl(process: subprocess.Popen | None, interface: str | None) -> bool:
    clean = True
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                clean = False
    try:
        if interface is None:
            return clean
        link = _run(["ip", "link", "show", "dev", interface], timeout=3, check=False)
        if link.returncode == 0:
            clean = _run(["ip", "link", "delete", interface], timeout=5, check=False).returncode == 0
    except RuntimeError:
        clean = False
    return clean


def _openvpn_target_ips(
    body: WorkerTestRequest | WorkerSessionOpenRequest,
) -> tuple[str, ...]:
    if isinstance(body, WorkerTestRequest):
        return (body.target_ip,)
    return tuple(body.target_ips)


def _start_openvpn(
    secret: dict,
    body: WorkerTestRequest | WorkerSessionOpenRequest,
    directory: Path,
) -> tuple[subprocess.Popen, str, str]:
    config, username, password = _openvpn_profile(secret, body)
    target_ips = _openvpn_target_ips(body)
    auth_path = directory / "auth.txt"
    config_path = directory / "client.conf"
    auth_path.write_text(f"{username}\n{password}\n", encoding="utf-8")
    os.chmod(auth_path, 0o600)
    config_path.write_text(
        render_openvpn_runtime_config(
            config,
            str(auth_path),
            target_ips,
        ),
        encoding="utf-8",
    )
    os.chmod(config_path, 0o600)
    previous_links = _ssl_links()
    process = subprocess.Popen(
        [settings.openvpn, "--config", str(config_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
    )
    timeout = settings.mfa_timeout if body.effective_mfa_mode != "none" else settings.connect_timeout
    deadline = time.monotonic() + timeout
    candidate_interface = None
    candidate_address = None
    while time.monotonic() < deadline and process.poll() is None:
        interface, assigned = _ssl_address(previous_links)
        if interface and assigned:
            candidate_interface = interface
            candidate_address = assigned
            routes_ready = True
            for target in target_ips:
                route = _run(
                    ["ip", "-4", "route", "get", target],
                    timeout=3,
                    check=False,
                )
                if route.returncode != 0 or not re.search(
                    rf"\bdev\s+{re.escape(interface)}(?:\s|$)",
                    route.stdout,
                ):
                    routes_ready = False
                    break
            if routes_ready:
                return process, interface, assigned
        time.sleep(0.25)
    _cleanup_ssl(process, candidate_interface)
    if candidate_address is not None:
        raise WorkerError(
            503,
            "vpn_routes_not_installed",
            "The VPN tunnel did not install routes to the selected devices",
        )
    raise WorkerError(503, "vpn_tunnel_not_established", "The VPN tunnel did not establish")


def _expire_session(session_id: str) -> None:
    global active_session
    with session_guard:
        session = active_session
        if session is None or session.session_id != session_id:
            return
        active_session = None
    _cleanup_ssl(session.process, session.interface)
    shutil.rmtree(session.directory, ignore_errors=True)
    worker_lock.release()
    logger.warning(
        "vpn.worker_session_expired",
        extra={"event": "vpn.worker_session_expired", "connection_id": session.connection_id},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/session/open")
def open_session(
    body: WorkerSessionOpenRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    global active_session
    _authorized(authorization)
    if (body.driver, body.protocol) != ("openvpn", "openvpn"):
        raise WorkerError(
            422, "vpn_session_driver_unsupported",
            "Automated prophylaxis sessions currently require OpenVPN",
        )
    settings.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not worker_lock.acquire(blocking=False):
        raise WorkerError(409, "vpn_test_busy", "Another VPN operation is already running")
    directory = None
    process = None
    interface = None
    try:
        directory = Path(tempfile.mkdtemp(prefix="session-", dir=settings.runtime))
        openbao = OpenBaoService(OpenBaoClient(settings.openbao_url, timeout=10), settings.identity_file)
        secret = openbao.read_secret(body.secret_ref)
        process, interface, assigned = _start_openvpn(secret, body, directory)
        session = WorkerSession(
            session_id=uuid4().hex, connection_id=body.connection_id,
            process=process, interface=interface, assigned_address=assigned,
            directory=directory,
        )
        session.timer = threading.Timer(
            settings.session_timeout, _expire_session, args=(session.session_id,),
        )
        session.timer.daemon = True
        with session_guard:
            active_session = session
        session.timer.start()
        logger.info(
            "vpn.worker_session_opened",
            extra={
                "event": "vpn.worker_session_opened",
                "connection_id": body.connection_id,
                "assigned_address": assigned,
                "interface": interface,
                "target_count": len(body.target_ips),
            },
        )
        return {"session_id": session.session_id, "assigned_address": assigned}
    except Exception:
        if process is not None:
            _cleanup_ssl(process, interface)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        worker_lock.release()
        raise


@app.delete("/v1/session/{session_id}")
def close_session(
    session_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    global active_session
    _authorized(authorization)
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise WorkerError(404, "vpn_session_not_found", "VPN session was not found")
    with session_guard:
        session = active_session
        if session is None or session.session_id != session_id:
            raise WorkerError(404, "vpn_session_not_found", "VPN session was not found")
        active_session = None
    if session.timer is not None:
        session.timer.cancel()
    clean = _cleanup_ssl(session.process, session.interface)
    shutil.rmtree(session.directory, ignore_errors=True)
    worker_lock.release()
    if not clean:
        raise WorkerError(503, "vpn_cleanup_failed", "VPN cleanup did not complete cleanly")
    logger.info(
        "vpn.worker_session_closed",
        extra={"event": "vpn.worker_session_closed", "connection_id": session.connection_id},
    )
    return {"status": "disconnected"}


def _mfa_snapshot(operation: WorkerMFAOperation) -> dict:
    payload = {
        "operation_id": operation.operation_id,
        "state": operation.state,
    }
    if operation.result is not None:
        payload["result"] = operation.result
    return payload


def _run_mfa_operation(operation: WorkerMFAOperation) -> None:
    body = operation.body
    mfa_mode = body.effective_mfa_mode
    started = time.monotonic()
    charon = None
    failure_step = "read_secret"
    otp_requested = False
    unexpected_otp = False
    initiate_timed_out = False
    result = {
        "status": "failed", "tunnel_established": False, "target_reachable": False,
        "assigned_address": None, "failure_stage": "worker",
        "reason_code": "worker_execution_failed",
        "message": "The VPN worker could not complete the MFA test.",
    }
    try:
        openbao = OpenBaoService(
            OpenBaoClient(settings.openbao_url, timeout=10), settings.identity_file,
        )
        secret = openbao.read_secret(body.secret_ref)
        failure_step = "validate_profile"
        profile, credentials = _profile(secret, body)
        failure_step = "prepare_runtime"
        settings.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mfa-", dir=settings.runtime) as directory:
            directory_path = Path(directory)
            config_path = directory_path / "swanctl.conf"
            otp_request = directory_path / "otp-requested"
            otp_response = directory_path / "otp-response"
            daemon_path = settings.runtime / "profile.conf"
            operation.otp_response_file = otp_response
            config_path.write_text(
                render_swanctl(profile, credentials, (f"{body.target_ip}/32",)),
                encoding="utf-8",
            )
            daemon_path.write_text(
                render_fortinet_daemon_fragment(
                    profile.network_id,
                    otp_request_file=str(otp_request),
                    otp_response_file=str(otp_response),
                    otp_timeout=settings.mfa_timeout,
                ),
                encoding="utf-8",
            )
            os.chmod(config_path, 0o600)
            os.chmod(daemon_path, 0o600)
            charon = subprocess.Popen(
                [settings.charon], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
            )
            socket_path = Path("/run/charon.vici")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not socket_path.exists() and charon.poll() is None:
                time.sleep(0.1)
            if charon.poll() is not None or not socket_path.exists():
                raise RuntimeError("charon_start_failed")
            logger.info(
                "vpn.worker_mfa_runtime_ready",
                extra={
                    "event": "vpn.worker_mfa_runtime_ready",
                    "request_id": operation.request_id,
                    "connection_id": body.connection_id,
                    "mfa_mode": mfa_mode,
                },
            )
            failure_step = "load_connection"
            _run([settings.swanctl, "--load-conns", "--file", str(config_path)])
            failure_step = "load_credentials"
            _run([settings.swanctl, "--load-creds", "--file", str(config_path)])
            failure_step = "initiate_ike"
            negotiation_started = time.monotonic()
            initiate = subprocess.Popen(
                [
                    settings.swanctl, "--initiate", "--child", "vpn",
                    "--timeout", str(settings.mfa_timeout),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
            )
            if mfa_mode == "push":
                with mfa_guard:
                    operation.state = "waiting_for_push"
            logger.info(
                "vpn.worker_mfa_negotiation_started",
                extra={
                    "event": "vpn.worker_mfa_negotiation_started",
                    "request_id": operation.request_id,
                    "connection_id": body.connection_id,
                    "mfa_mode": mfa_mode,
                },
            )
            deadline = time.monotonic() + settings.mfa_timeout + 10
            while initiate.poll() is None and time.monotonic() < deadline:
                if otp_request.exists() and not otp_requested:
                    otp_requested = True
                    logger.info(
                        "vpn.worker_mfa_challenge_received",
                        extra={
                            "event": "vpn.worker_mfa_challenge_received",
                            "request_id": operation.request_id,
                            "connection_id": body.connection_id,
                            "mfa_mode": mfa_mode,
                        },
                    )
                    if mfa_mode == "push":
                        unexpected_otp = True
                        initiate.terminate()
                        break
                    with mfa_guard:
                        operation.state = "waiting_for_otp"
                time.sleep(0.1)
            initiate_timed_out = (
                time.monotonic() - negotiation_started >= settings.mfa_timeout - 1
            )
            if initiate.poll() is None:
                initiate.terminate()
                try:
                    initiate.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    initiate.kill()
                    initiate.wait(timeout=3)
            ike, child, assigned = _sas()
            result.update(tunnel_established=bool(ike and child), assigned_address=assigned)
            if unexpected_otp:
                result.update(
                    failure_stage="ike",
                    reason_code="unexpected_otp_challenge",
                    message="The gateway requested an OTP, but this VPN profile is configured for push approval.",
                )
            elif not ike:
                if mfa_mode == "otp" and not otp_requested and initiate_timed_out:
                    reason_code = "otp_challenge_not_received"
                    message = "The gateway did not request the configured OTP challenge."
                elif mfa_mode == "push" and initiate_timed_out:
                    reason_code = "push_approval_timeout"
                    message = "Push approval did not establish IKE before the MFA timeout."
                else:
                    reason_code = "ike_authentication_failed"
                    message = "IKE authentication did not establish."
                result.update(failure_stage="ike", reason_code=reason_code, message=message)
            elif not child:
                result.update(
                    failure_stage="child", reason_code="child_sa_not_installed",
                    message="IKE established, but the CHILD SA was not installed.",
                )
            else:
                try:
                    with socket.create_connection(
                        (body.target_ip, body.target_port),
                        timeout=settings.target_timeout,
                        source_address=((assigned, 0) if assigned else None),
                    ):
                        pass
                    result.update(
                        status="passed", target_reachable=True, failure_stage=None,
                        reason_code=None,
                        message="The tunnel established and the selected device accepted the TCP connection.",
                    )
                except OSError:
                    result.update(
                        failure_stage="target", reason_code="target_unreachable",
                        message="The tunnel established, but the selected TCP target did not answer.",
                    )
    except (FortiClientProfileError, OpenVPNProfileError):
        result.update(
            failure_stage="validation", reason_code="profile_validation_failed",
            message="The stored VPN profile is not supported by this worker.",
        )
    except Exception as exc:
        reason_code = {
            "read_secret": "vpn_secret_unavailable",
            "validate_profile": "profile_validation_failed",
            "prepare_runtime": "worker_runtime_failed",
            "load_connection": "ike_configuration_load_failed",
            "load_credentials": "ike_credentials_load_failed",
            "initiate_ike": "ike_initiation_failed",
        }.get(failure_step, "worker_execution_failed")
        result.update(reason_code=reason_code)
        logger.warning(
            "vpn.worker_mfa_execution_failed",
            extra={
                "event": "vpn.worker_mfa_execution_failed",
                "request_id": operation.request_id,
                "connection_id": body.connection_id,
                "reason_code": reason_code,
                "failure_step": failure_step,
                "exception_type": type(exc).__name__,
            },
        )
    finally:
        clean = _cleanup(charon)
        try:
            (settings.runtime / "profile.conf").unlink(missing_ok=True)
        except OSError:
            clean = False
        if not clean:
            result.update(
                status="failed", failure_stage="cleanup",
                reason_code="cleanup_failed",
                message="VPN cleanup did not complete cleanly.",
            )
        result["disconnected"] = clean
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        with mfa_guard:
            operation.result = result
            operation.state = "finished"
            operation.otp_response_file = None
        worker_lock.release()
        logger.info(
            "vpn.worker_mfa_finished",
            extra={
                "event": "vpn.worker_mfa_finished",
                "request_id": operation.request_id,
                "connection_id": body.connection_id,
                "status": result["status"],
                "failure_stage": result["failure_stage"],
                "reason_code": result.get("reason_code"),
                "mfa_mode": mfa_mode,
                "disconnected": result["disconnected"],
                "duration_ms": result["duration_ms"],
            },
        )


@app.post("/v1/test/mfa")
def start_mfa_test(
    body: WorkerTestRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _authorized(authorization)
    if body.effective_mfa_mode == "none" or (body.driver, body.protocol) != ("strongswan", "ipsec-ikev2"):
        raise WorkerError(422, "vpn_worker_request_rejected", "This operation requires IPsec MFA")
    if not worker_lock.acquire(blocking=False):
        raise WorkerError(409, "vpn_test_busy", "Another VPN operation is already running")
    operation = WorkerMFAOperation(
        operation_id=uuid4().hex,
        request_id=_request_id(request),
        body=body,
    )
    with mfa_guard:
        finished = [key for key, value in mfa_operations.items() if value.state == "finished"]
        for key in finished[:-9]:
            mfa_operations.pop(key, None)
        mfa_operations[operation.operation_id] = operation
    thread = threading.Thread(target=_run_mfa_operation, args=(operation,), daemon=True)
    thread.start()
    return _mfa_snapshot(operation)


@app.get("/v1/test/mfa/{operation_id}")
def get_mfa_test(
    operation_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _authorized(authorization)
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise WorkerError(404, "vpn_mfa_operation_not_found", "MFA operation was not found")
    with mfa_guard:
        operation = mfa_operations.get(operation_id)
        if operation is None:
            raise WorkerError(404, "vpn_mfa_operation_not_found", "MFA operation was not found")
        return _mfa_snapshot(operation)


@app.post("/v1/test/mfa/{operation_id}/otp")
def submit_mfa_otp(
    operation_id: str,
    body: WorkerOTPRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _authorized(authorization)
    with mfa_guard:
        operation = mfa_operations.get(operation_id)
        if operation is None:
            raise WorkerError(404, "vpn_mfa_operation_not_found", "MFA operation was not found")
        if operation.body.connection_id != body.connection_id:
            raise WorkerError(404, "vpn_mfa_operation_not_found", "MFA operation was not found")
        if operation.state != "waiting_for_otp" or operation.otp_response_file is None:
            raise WorkerError(409, "vpn_mfa_not_waiting", "MFA operation is not waiting for a token")
        response_file = operation.otp_response_file
        code = body.code.get_secret_value().encode("ascii")
        try:
            descriptor = os.open(
                response_file,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.write(descriptor, code)
            finally:
                os.close(descriptor)
        except OSError:
            raise WorkerError(409, "vpn_mfa_not_waiting", "MFA token could not be accepted") from None
        operation.state = "authenticating"
        return _mfa_snapshot(operation)


@app.post("/v1/test")
def test_vpn(
    body: WorkerTestRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _authorized(authorization)
    if not worker_lock.acquire(blocking=False):
        raise WorkerError(409, "vpn_test_busy", "Another VPN test is already running")
    request_id = _request_id(request)
    logger.info(
        "vpn.worker_test_started",
        extra={
            "event": "vpn.worker_test_started",
            "request_id": request_id,
            "connection_id": body.connection_id,
            "driver": body.driver,
            "protocol": body.protocol,
            "target_port": body.target_port,
        },
    )
    started = time.monotonic()
    charon = None
    ssl_process = None
    ssl_interface = None
    cleanup_kind = "ipsec" if body.driver == "strongswan" else "ssl"
    result = {
        "status": "failed", "tunnel_established": False, "target_reachable": False,
        "assigned_address": None, "failure_stage": "worker",
        "message": "The VPN worker could not complete the test.",
    }
    try:
        openbao = OpenBaoService(OpenBaoClient(settings.openbao_url, timeout=10), settings.identity_file)
        secret = openbao.read_secret(body.secret_ref)
        if (body.driver, body.protocol) not in {
            ("strongswan", "ipsec-ikev2"), ("openfortivpn", "ssl-vpn"),
            ("openvpn", "openvpn"),
        }:
            raise FortiClientProfileError("unsupported_profile_type")
        if body.driver in {"openfortivpn", "openvpn"}:
            settings.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="test-", dir=settings.runtime) as directory:
                directory_path = Path(directory)
                config_path = directory_path / "client.conf"
                if body.driver == "openvpn":
                    ssl_process, ssl_interface, assigned = _start_openvpn(
                        secret, body, directory_path,
                    )
                    rendered = None
                else:
                    profile, username, password = _ssl_profile(secret, body)
                    rendered = _render_ssl_config(profile, username, password)
                    arguments = [settings.openfortivpn, "-c", str(config_path)]
                if rendered is not None:
                    config_path.write_text(rendered, encoding="utf-8")
                    os.chmod(config_path, 0o600)
                    previous_links = _ssl_links()
                    ssl_process = subprocess.Popen(
                        arguments,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, close_fds=True,
                    )
                    connection_timeout = (
                        settings.mfa_timeout
                        if body.effective_mfa_mode != "none"
                        else settings.connect_timeout
                    )
                    deadline = time.monotonic() + connection_timeout
                    assigned = None
                    while time.monotonic() < deadline and ssl_process.poll() is None:
                        ssl_interface, assigned = _ssl_address(previous_links)
                        if assigned:
                            break
                        time.sleep(0.25)
                if not assigned:
                    result.update(failure_stage="tunnel", message="The VPN tunnel did not establish.")
                else:
                    result.update(tunnel_established=True, assigned_address=assigned)
                    try:
                        with socket.create_connection(
                            (body.target_ip, body.target_port), timeout=settings.target_timeout,
                            source_address=(assigned, 0),
                        ):
                            pass
                        result.update(status="passed", target_reachable=True, failure_stage=None,
                                      message="The tunnel established and the selected device accepted the TCP connection.")
                    except OSError:
                        result.update(failure_stage="target",
                                      message="The tunnel established, but the selected TCP target did not answer.")
            return result

        profile, credentials = _profile(secret, body)
        connection_timeout = (
            settings.mfa_timeout
            if body.effective_mfa_mode != "none"
            else settings.connect_timeout
        )
        # The requested proof target is the only customer prefix installed for this test.
        swanctl_config = render_swanctl(profile, credentials, (f"{body.target_ip}/32",))
        daemon_config = render_fortinet_daemon_fragment(profile.network_id)
        settings.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="test-", dir=settings.runtime) as directory:
            directory_path = Path(directory)
            config_path = directory_path / "swanctl.conf"
            daemon_path = settings.runtime / "profile.conf"
            config_path.write_text(swanctl_config, encoding="utf-8")
            daemon_path.write_text(daemon_config, encoding="utf-8")
            os.chmod(config_path, 0o600)
            os.chmod(daemon_path, 0o600)
            charon = subprocess.Popen(
                [settings.charon], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
            )
            socket_path = Path("/run/charon.vici")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not socket_path.exists() and charon.poll() is None:
                time.sleep(0.1)
            if charon.poll() is not None or not socket_path.exists():
                raise RuntimeError("charon_start_failed")
            _run([settings.swanctl, "--load-conns", "--file", str(config_path)])
            _run([settings.swanctl, "--load-creds", "--file", str(config_path)])
            deadline = time.monotonic() + connection_timeout
            _run(
                [
                    settings.swanctl, "--initiate", "--child", "vpn",
                    "--timeout", str(connection_timeout),
                ],
                timeout=connection_timeout + 5, check=False,
            )
            ike, child, assigned = _sas()
            # A push-MFA exchange may continue in charon after the VICI command
            # returns. Poll the existing negotiation only; never re-initiate it,
            # because that could generate duplicate push notifications.
            while (
                body.effective_mfa_mode != "none"
                and not (ike and child)
                and time.monotonic() < deadline
            ):
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
                ike, child, assigned = _sas()
            result.update(tunnel_established=bool(ike and child), assigned_address=assigned)
            if not ike:
                result.update(failure_stage="ike", message="IKE authentication did not establish.")
            elif not child:
                result.update(failure_stage="child", message="IKE established, but the CHILD SA was not installed.")
            else:
                try:
                    with socket.create_connection(
                        (body.target_ip, body.target_port),
                        timeout=settings.target_timeout,
                        source_address=((assigned, 0) if assigned else None),
                    ):
                        pass
                    result.update(
                        status="passed", target_reachable=True, failure_stage=None,
                        message="The tunnel established and the selected device accepted the TCP connection.",
                    )
                except OSError:
                    result.update(
                        failure_stage="target",
                        message="The tunnel established, but the selected TCP target did not answer.",
                    )
    except (FortiClientProfileError, OpenVPNProfileError):
        logger.warning(
            "vpn.worker_test_validation_failed",
            extra={
                "event": "vpn.worker_test_validation_failed",
                "request_id": request_id,
                "connection_id": body.connection_id,
                "reason_code": "unsupported_profile_type",
            },
        )
        result.update(failure_stage="validation", message="The stored VPN profile is not supported by this worker.")
    except Exception as exc:
        # Never copy exception text because it could include dependency details.
        logger.warning(
            "vpn.worker_test_execution_failed",
            extra={
                "event": "vpn.worker_test_execution_failed",
                "request_id": request_id,
                "connection_id": body.connection_id,
                "reason_code": "unexpected_execution_error",
                "exception_type": type(exc).__name__,
            },
        )
        result.update(failure_stage=result.get("failure_stage") or "worker")
    finally:
        clean = (_cleanup(charon) if cleanup_kind == "ipsec"
                 else _cleanup_ssl(ssl_process, ssl_interface))
        try:
            (settings.runtime / "profile.conf").unlink(missing_ok=True)
        except OSError:
            clean = False
        if not clean:
            result.update(status="failed", failure_stage="cleanup", message="VPN cleanup did not complete cleanly.")
        result["disconnected"] = clean
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        logger.info(
            "vpn.worker_test_finished",
            extra={
                "event": "vpn.worker_test_finished",
                "request_id": request_id,
                "connection_id": body.connection_id,
                "status": result["status"],
                "failure_stage": result["failure_stage"],
                "disconnected": result["disconnected"],
                "duration_ms": result["duration_ms"],
            },
        )
        worker_lock.release()
    return result


__all__ = ["app"]
