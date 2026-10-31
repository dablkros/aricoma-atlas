"""Extract FortiClient profile material without logging or retaining credentials.

This module does not connect VPNs. The EncX variant is supported only when the
integrity check verifies with the supplied export password.
"""

from dataclasses import dataclass, field
import hashlib
import hmac
import plistlib
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from defusedxml import ElementTree as SafeET

_MAX_BYTES = 2 * 1024 * 1024
_KEY = b"fc_1A2Brown3Fox4Jumped5Over6A7Lazy8Dog"


class FortiClientProfileError(ValueError):
    """Reason codes contain no profile or secret material."""


@dataclass(frozen=True)
class FortiClientProfile:
    name: str
    gateway: str
    network_id: int
    ike_version: int
    gateway_port: int
    local_id: str | None
    user_auth_enabled: bool
    phase1_proposals: tuple[str, ...]
    phase1_dh_group: int
    phase2_proposals: tuple[str, ...]
    phase2_dh_group: int
    pfs: bool
    encrypted_auth_info: str = field(repr=False)


@dataclass(frozen=True)
class FortiClientSSLProfile:
    name: str
    gateway: str
    gateway_port: int
    realm: str | None
    sso_enabled: bool


def _integer(value, name, minimum, maximum):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = int(str(value).rstrip(';'))
    except (TypeError, ValueError):
        raise FortiClientProfileError(f"invalid_{name}") from None
    if not minimum <= number <= maximum:
        raise FortiClientProfileError(f"invalid_{name}")
    return number


def _required(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise FortiClientProfileError(f"invalid_{name}")
    return value.strip()


def _plist(data, name):
    try:
        if not data.startswith(b'bplist00'):
            SafeET.fromstring(data)
        profiles = plistlib.loads(data)["Profiles"]
        value = profiles[name]
        if not isinstance(value, dict):
            raise ValueError()
        if value.get("AuthMethod") != 0 or value.get("SSOEnabled") != 0:
            raise FortiClientProfileError("unsupported_auth_method")
        return FortiClientProfile(
            name=name,
            gateway=_required(value.get("Server"), "gateway"),
            network_id=_integer(value.get("NetworkID"), "network_id", 0, 255),
            ike_version=_integer(value.get("IKEVersion"), "ike_version", 2, 2),
            gateway_port=_integer(value.get("UdpPort"), "gateway_port", 1, 65535),
            local_id=value.get("LocalID") or None,
            user_auth_enabled=_integer(value.get("EnableXAuth"), "user_auth", 0, 1) == 1,
            phase1_proposals=tuple(_required(value.get("Phase1Proposals"), "phase1").split(',')),
            phase1_dh_group=_integer(value.get("Phase1DHGroup"), "phase1_dh", 1, 65535),
            phase2_proposals=tuple(_required(value.get("Phase2Proposals"), "phase2").split(',')),
            phase2_dh_group=_integer(value.get("Phase2DHGroup"), "phase2_dh", 1, 65535),
            pfs=_integer(value.get("Phase2PFSEnabled"), "pfs", 0, 1) == 1,
            encrypted_auth_info=_required(value.get("AuthInfo"), "auth_info"),
        )
    except FortiClientProfileError:
        raise
    except Exception:
        raise FortiClientProfileError("invalid_or_missing_profile") from None


def _xml_root(data):
    try:
        return SafeET.fromstring(data)
    except Exception:
        raise FortiClientProfileError("invalid_or_missing_profile") from None


def _xml_ipsec(root, name):
    try:
        matches = [entry for entry in root.iter('connection')
                   if entry.find('ike_settings') is not None
                   and (entry.findtext('name') or '').strip() == name]
        if len(matches) != 1:
            raise FortiClientProfileError("ambiguous_or_missing_profile")
        c = matches[0]
        i, p = c.find('ike_settings'), c.find('ipsec_settings')
        if i is None or p is None:
            raise FortiClientProfileError("invalid_profile")
        if i.findtext('authentication_method') != 'Preshared Key':
            raise FortiClientProfileError("unsupported_auth_method")
        if _integer(i.findtext('sso_enabled'), 'sso', 0, 1):
            raise FortiClientProfileError("unsupported_auth_method")
        def proposals(parent):
            result = tuple((e.text or '').strip() for e in parent.findall('proposals/proposal'))
            if not result or any(not v or len(v) > 128 for v in result):
                raise FortiClientProfileError("invalid_proposals")
            return result
        return FortiClientProfile(
            name=name, gateway=_required(i.findtext('server'), 'gateway'),
            network_id=_integer(i.findtext('networkid'), 'network_id', 0, 255),
            ike_version=_integer(i.findtext('version'), 'ike_version', 2, 2),
            gateway_port=_integer(i.findtext('udp_port'), 'gateway_port', 1, 65535),
            local_id=i.findtext('localid') or None,
            user_auth_enabled=_integer(i.findtext('xauth/enabled'), 'user_auth', 0, 1) == 1,
            phase1_proposals=proposals(i),
            phase1_dh_group=_integer(i.findtext('dhgroup'), 'phase1_dh', 1, 65535),
            phase2_proposals=proposals(p),
            phase2_dh_group=_integer(p.findtext('dhgroup'), 'phase2_dh', 1, 65535),
            pfs=_integer(p.findtext('pfs'), 'pfs', 0, 1) == 1,
            encrypted_auth_info=_required(i.findtext('auth_data/preshared_key'), 'auth_info'),
        )
    except FortiClientProfileError:
        raise
    except Exception:
        raise FortiClientProfileError("invalid_or_missing_profile") from None


def _ssl_server(value):
    value = _required(value, "gateway")
    if value.count(":") > 1:
        raise FortiClientProfileError("unsupported_ssl_gateway")
    if ":" not in value:
        return value, 443
    host, port = value.rsplit(":", 1)
    return _required(host, "gateway"), _integer(port, "gateway_port", 1, 65535)


def _xml_ssl(root, name):
    matches = [entry for entry in root.iter('connection')
               if entry.find('ike_settings') is None
               and entry.find('server') is not None
               and (entry.findtext('name') or '').strip() == name]
    if len(matches) != 1:
        raise FortiClientProfileError("ambiguous_or_missing_profile")
    entry = matches[0]
    gateway, port = _ssl_server(entry.findtext('server'))
    realm = (entry.findtext('realm') or '').strip() or None
    if realm is not None:
        realm = _required(realm, "realm")
    sso_text = (entry.findtext('sso_enabled') or '0').strip()
    return FortiClientSSLProfile(
        name=name, gateway=gateway, gateway_port=port, realm=realm,
        sso_enabled=_integer(sso_text, "sso", 0, 1) == 1,
    )


def parse_forticlient_any_profile(data: bytes, name: str, *, format: str):
    if not isinstance(data, bytes) or len(data) > _MAX_BYTES:
        raise FortiClientProfileError("invalid_profile_size")
    name = _required(name, "profile_name")
    if format == 'macos-plist':
        return _plist(data, name)
    if format != 'windows-xml':
        raise FortiClientProfileError("unsupported_profile_format")
    root = _xml_root(data)
    ipsec = [entry for entry in root.iter('connection')
             if entry.find('ike_settings') is not None
             and (entry.findtext('name') or '').strip() == name]
    ssl = [entry for entry in root.iter('connection')
           if entry.find('ike_settings') is None and entry.find('server') is not None
           and (entry.findtext('name') or '').strip() == name]
    if bool(ipsec) == bool(ssl):
        raise FortiClientProfileError("ambiguous_or_missing_profile")
    return _xml_ipsec(root, name) if ipsec else _xml_ssl(root, name)


def parse_forticlient_profile(data: bytes, name: str, *, format: str):
    profile = parse_forticlient_any_profile(data, name, format=format)
    if not isinstance(profile, FortiClientProfile):
        raise FortiClientProfileError("unsupported_profile_type")
    return profile


def _blob(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r" [0-9A-Fa-f]+", value) or len(value) > 16384:
        raise FortiClientProfileError("invalid_encrypted_secret")
    try:
        return bytes.fromhex(value[len(prefix) + 1:])
    except ValueError:
        raise FortiClientProfileError("invalid_encrypted_secret") from None


def _decode(blob, password, key_lengths):
    matched = []
    for key_length in key_lengths:
        salt_length = key_length // 2
        if len(blob) <= salt_length + 12:
            continue
        salt, verifier = blob[:salt_length], blob[salt_length:salt_length + 2]
        ciphertext, tag = blob[salt_length + 2:-10], blob[-10:]
        key = hashlib.pbkdf2_hmac('sha1', password, salt, 1000, 2 * key_length + 2)
        mac = hmac.new(key[key_length:2 * key_length], ciphertext, hashlib.sha1).digest()[:10]
        if hmac.compare_digest(verifier, key[2 * key_length:]) and hmac.compare_digest(tag, mac):
            matched.append((key[:key_length], ciphertext))
    if len(matched) != 1:
        raise FortiClientProfileError("encrypted_secret_integrity_failed")
    key, ciphertext = matched[0]
    counters = b''.join((i + 1).to_bytes(16, 'little') for i in range((len(ciphertext) + 15) // 16))
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    blocks = encryptor.update(counters) + encryptor.finalize()
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, blocks))
    if plaintext.endswith(b'\0'):
        plaintext = plaintext[:-1]
    try:
        printable = plaintext.decode('utf-8').isprintable()
    except UnicodeError:
        printable = False
    if not plaintext or not printable:
        raise FortiClientProfileError("invalid_decrypted_secret")
    return plaintext


def decode_macos_enc(value: str) -> bytes:
    """Decode the authenticated Enc variant; its semantic role is caller-owned."""
    if isinstance(value, str) and value.startswith('EncX '):
        raise FortiClientProfileError("unsupported_encrypted_secret_format")
    return _decode(_blob(value, 'Enc'), _KEY, (24,))


def decode_windows_encx(value: str, export_password: str) -> bytes:
    """Decode only a verified EncX layout, using a supplied UTF-8 export password."""
    if not isinstance(export_password, str) or not export_password or len(export_password) > 1024:
        raise FortiClientProfileError("export_password_required")
    return _decode(_blob(value, 'EncX'), export_password.encode('utf-8'), (16, 24, 32))
