"""Strict parsing and runtime rendering for imported OpenVPN client profiles."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re


class OpenVPNProfileError(ValueError):
    pass


_ALLOWED_FLAGS = {
    "client", "nobind", "persist-key", "persist-tun",
}
_ALLOWED_VALUES = {
    "dev", "proto", "verify-x509-name", "route", "resolv-retry",
    "auth-user-pass", "cipher", "auth", "route-delay", "verb",
    "reneg-sec", "remote",
}
_INLINE_BLOCKS = {"ca", "cert", "key"}
_TOKEN = re.compile(r"[^\s\x00-\x1f\x7f]+")
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._ -]+")


@dataclass(frozen=True)
class OpenVPNProfile:
    name: str
    gateway: str
    gateway_port: int
    protocol: str
    canonical_config: str


def _profile_name(source_name: str | None) -> str:
    name = (source_name or "OpenVPN").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if name.lower().endswith(".ovpn"):
        name = name[:-5]
    name = _SAFE_NAME.sub("-", name).strip(" .-")
    return name[:200] or "OpenVPN"


def _tokens(line: str) -> list[str]:
    values = line.split()
    if not values or any(not _TOKEN.fullmatch(value) for value in values):
        raise OpenVPNProfileError("vpn_profile_invalid")
    return values


def parse_openvpn_profile(data: bytes | str, *, source_name: str | None = None) -> OpenVPNProfile:
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
    except UnicodeDecodeError:
        raise OpenVPNProfileError("vpn_profile_invalid") from None
    if not isinstance(text, str) or "\x00" in text:
        raise OpenVPNProfileError("vpn_profile_invalid")

    directives: list[list[str]] = []
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if current is not None:
            if line == f"</{current}>":
                current = None
            elif line.startswith("<") and line.endswith(">"):
                raise OpenVPNProfileError("vpn_profile_invalid")
            elif line:
                blocks[current].append(line)
            continue
        if not line or line.startswith(("#", ";")):
            continue
        block = re.fullmatch(r"<([a-z-]+)>", line)
        if block:
            name = block.group(1)
            if name not in _INLINE_BLOCKS or name in blocks:
                raise OpenVPNProfileError("vpn_profile_unsupported_directive")
            blocks[name] = []
            current = name
            continue
        values = _tokens(line)
        name = values[0].lower()
        if name in _ALLOWED_FLAGS:
            if len(values) != 1:
                raise OpenVPNProfileError("vpn_profile_invalid")
        elif name in _ALLOWED_VALUES:
            if len(values) < 2 and name != "auth-user-pass":
                raise OpenVPNProfileError("vpn_profile_invalid")
            if name == "auth-user-pass" and len(values) != 1:
                raise OpenVPNProfileError("vpn_profile_external_reference")
        else:
            raise OpenVPNProfileError("vpn_profile_unsupported_directive")
        directives.append(values)
    if current is not None or set(blocks) != _INLINE_BLOCKS or any(not blocks[x] for x in blocks):
        raise OpenVPNProfileError("vpn_profile_invalid")

    grouped: dict[str, list[list[str]]] = {}
    for values in directives:
        grouped.setdefault(values[0].lower(), []).append(values)
    for required in ("client", "dev", "proto", "remote", "auth-user-pass"):
        if len(grouped.get(required, ())) != 1:
            raise OpenVPNProfileError("vpn_profile_invalid")
    if grouped["dev"][0][1] != "tun":
        raise OpenVPNProfileError("vpn_profile_unsupported_device")
    protocol = grouped["proto"][0][1].lower()
    if protocol not in {"udp", "udp4", "tcp", "tcp4", "tcp-client"}:
        raise OpenVPNProfileError("vpn_profile_unsupported_protocol")
    remote = grouped["remote"][0]
    if len(remote) not in {2, 3, 4}:
        raise OpenVPNProfileError("vpn_profile_invalid")
    gateway = remote[1]
    if len(gateway) > 253:
        raise OpenVPNProfileError("vpn_profile_invalid")
    try:
        port = int(remote[2]) if len(remote) >= 3 else 1194
    except ValueError:
        raise OpenVPNProfileError("vpn_profile_invalid") from None
    if not 1 <= port <= 65535:
        raise OpenVPNProfileError("vpn_profile_invalid")
    for route in grouped.get("route", ()):
        if len(route) not in {2, 3, 4, 5}:
            raise OpenVPNProfileError("vpn_profile_invalid")
        try:
            if route[1] == "remote_host":
                if len(route) < 3:
                    raise ValueError()
                ipaddress.ip_address(route[2])
            elif len(route) == 2:
                ipaddress.ip_network(route[1], strict=False)
            else:
                ipaddress.ip_network(f"{route[1]}/{route[2]}", strict=False)
            if len(route) >= 4 and route[3] not in {
                "vpn_gateway", "net_gateway", "remote_host",
            }:
                ipaddress.ip_address(route[3])
            if len(route) == 5 and int(route[4]) < 0:
                raise ValueError()
        except ValueError:
            raise OpenVPNProfileError("vpn_profile_invalid") from None

    canonical: list[str] = [" ".join(values) for values in directives]
    for name in ("ca", "cert", "key"):
        canonical.extend((f"<{name}>", *blocks[name], f"</{name}>"))
    return OpenVPNProfile(
        name=_profile_name(source_name), gateway=gateway, gateway_port=port,
        protocol=protocol, canonical_config="\n".join(canonical) + "\n",
    )


def render_openvpn_runtime_config(config: str, auth_path: str) -> str:
    """Revalidate stored input and bind auth-user-pass to a protected runtime file."""
    profile = parse_openvpn_profile(config)
    if not auth_path.startswith("/") or any(character in auth_path for character in "\r\n\x00 "):
        raise OpenVPNProfileError("vpn_profile_invalid")
    lines = []
    replaced = False
    for line in profile.canonical_config.splitlines():
        if line == "auth-user-pass":
            lines.append(f"auth-user-pass {auth_path}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        raise OpenVPNProfileError("vpn_profile_invalid")
    lines.append("auth-nocache")
    return "\n".join(lines) + "\n"
