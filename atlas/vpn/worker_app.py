"""Privileged, single-operation VPN worker with mandatory teardown."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas.openbao_client import OpenBaoClient
from atlas.services.openbao import OpenBaoService
from atlas.vpn.forticlient_profile import FortiClientProfile, FortiClientProfileError
from atlas.vpn.strongswan_config import (
    VPNSecrets,
    render_fortinet_daemon_fragment,
    render_swanctl,
)


class WorkerTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    tenant_id: int = Field(gt=0)
    secret_ref: str = Field(pattern=r"^vpn/connections/[a-z0-9]+(?:-[a-z0-9]+)*$")
    gateway_host: str = Field(min_length=1, max_length=253)
    gateway_port: int = Field(ge=1, le=65535)
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
        self.target_timeout = float(os.environ.get("ATLAS_VPN_TARGET_TIMEOUT", "5"))


settings = WorkerSettings()
worker_lock = threading.Lock()
app = FastAPI(title="Atlas VPN worker", docs_url=None, redoc_url=None, openapi_url=None)


def _token() -> str:
    try:
        value = settings.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise HTTPException(503, "worker identity unavailable") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value):
        raise HTTPException(503, "worker identity unavailable")
    return value


def _authorized(authorization: str | None) -> None:
    import hmac
    expected = f"Bearer {_token()}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "unauthorized")


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
    if secret.get("schema_version") != 1 or secret.get("connection_id") != body.connection_id or secret.get("tenant_id") != body.tenant_id:
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/test")
def test_vpn(body: WorkerTestRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorized(authorization)
    if not worker_lock.acquire(blocking=False):
        raise HTTPException(409, "another VPN test is active")
    started = time.monotonic()
    charon = None
    result = {
        "status": "failed", "tunnel_established": False, "target_reachable": False,
        "assigned_address": None, "failure_stage": "worker",
        "message": "The VPN worker could not complete the test.",
    }
    try:
        openbao = OpenBaoService(OpenBaoClient(settings.openbao_url, timeout=10), settings.identity_file)
        secret = openbao.read_secret(body.secret_ref)
        profile, credentials = _profile(secret, body)
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
            initiated = _run(
                [settings.swanctl, "--initiate", "--child", "vpn"],
                timeout=settings.connect_timeout, check=False,
            )
            ike, child, assigned = _sas()
            result.update(tunnel_established=bool(ike and child), assigned_address=assigned)
            if not ike:
                result.update(failure_stage="ike", message="IKE authentication did not establish.")
            elif not child:
                result.update(failure_stage="child", message="IKE established, but the CHILD SA was not installed.")
            elif initiated.returncode != 0:
                result.update(failure_stage="child", message="The VPN negotiation did not complete cleanly.")
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
    except FortiClientProfileError:
        result.update(failure_stage="validation", message="The stored VPN profile is not supported by this worker.")
    except Exception:
        # Never copy exception text because it could include dependency details.
        result.update(failure_stage=result.get("failure_stage") or "worker")
    finally:
        clean = _cleanup(charon)
        try:
            (settings.runtime / "profile.conf").unlink(missing_ok=True)
        except OSError:
            clean = False
        if not clean:
            result.update(status="failed", failure_stage="cleanup", message="VPN cleanup did not complete cleanly.")
        result["disconnected"] = clean
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        worker_lock.release()
    return result


__all__ = ["app"]
