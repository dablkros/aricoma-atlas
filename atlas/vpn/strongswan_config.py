"""Render a narrow FortiClient IKEv2 configuration without persistence or I/O."""

import base64
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import re

from atlas.vpn.forticlient_profile import FortiClientProfile, FortiClientProfileError

_IKE = {
    'AES256GCM|PRFSHA512': 'aes256gcm16-prfsha512-ecp521',
    'AES256|SHA256': 'aes256-sha256-ecp521',
    'AES256|SHA512': 'aes256-sha512-ecp521',
}
_ESP = {
    'AES256GCM|NONE': 'aes256gcm16-ecp521',
    'AES256|SHA256': 'aes256-sha256-ecp521',
    'AES256|SHA512': 'aes256-sha512-ecp521',
}


@dataclass(frozen=True)
class VPNSecrets:
    username: str = field(repr=False)
    password: str = field(repr=False)
    psk: bytes = field(repr=False)


def render_fortinet_daemon_fragment(network_id: int) -> str:
    """Render a per-worker fragment for Atlas' strongSwan 6.1.0 lab patch."""
    if isinstance(network_id, bool) or not isinstance(network_id, int) or not 0 <= network_id <= 255:
        raise FortiClientProfileError('invalid_network_id')
    return f'charon {{\n    atlas_fortinet_network_id = {network_id}\n}}\n'


def _identity(value, reason):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,255}', value):
        raise FortiClientProfileError(reason)
    return value


def _gateway(value):
    try:
        return str(ip_address(value))
    except ValueError:
        labels = value.lower().rstrip('.').split('.')
        if not value or len(value) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
            raise FortiClientProfileError('invalid_gateway') from None
        return '.'.join(labels)


def _proposals(values, mapping):
    try:
        normalized = tuple(dict.fromkeys(mapping[value.strip().upper()] for value in values))
    except (KeyError, AttributeError):
        raise FortiClientProfileError('unsupported_proposal') from None
    if not normalized:
        raise FortiClientProfileError('unsupported_proposal')
    return ','.join(normalized)


def validate_profile(profile: FortiClientProfile, secrets: VPNSecrets) -> None:
    """Validate the supported profile independently of runtime target selection."""
    if profile.ike_version != 2 or profile.phase1_dh_group != 21 or profile.phase2_dh_group != 21 or not profile.pfs or not profile.user_auth_enabled:
        raise FortiClientProfileError('unsupported_ipsec_profile')
    if not isinstance(secrets.psk, bytes) or not secrets.psk or not isinstance(secrets.password, str) or not secrets.password:
        raise FortiClientProfileError('invalid_credentials')
    _identity(secrets.username, 'unsupported_username')
    if profile.local_id:
        _identity(profile.local_id, 'unsupported_local_id')
    _proposals(profile.phase1_proposals, _IKE)
    _proposals(profile.phase2_proposals, _ESP)
    _gateway(profile.gateway)
    if not 1 <= profile.gateway_port <= 65535 or not 0 <= profile.network_id <= 255:
        raise FortiClientProfileError('invalid_profile')


def render_swanctl(profile: FortiClientProfile, secrets: VPNSecrets,
                   allowed_prefixes: tuple[str, ...]) -> str:
    """Return sensitive config; caller must use private transient storage only.

    The caller must separately configure and verify a dedicated patched daemon
    with profile.network_id. The return value must never be exposed or logged.
    """
    validate_profile(profile, secrets)
    username = _identity(secrets.username, 'unsupported_username')
    local_id = _identity(profile.local_id, 'unsupported_local_id') if profile.local_id else None
    if not allowed_prefixes:
        raise FortiClientProfileError('allowed_prefixes_required')
    try:
        networks = tuple(dict.fromkeys(str(ip_network(item, strict=True)) for item in allowed_prefixes))
        if any(ip_network(item).version != 4 for item in networks):
            raise ValueError()
    except (TypeError, ValueError):
        raise FortiClientProfileError('unsupported_allowed_prefix') from None
    ike = _proposals(profile.phase1_proposals, _IKE)
    esp = _proposals(profile.phase2_proposals, _ESP)
    gateway = _gateway(profile.gateway)
    if not 1 <= profile.gateway_port <= 65535 or not 0 <= profile.network_id <= 255:
        raise FortiClientProfileError('invalid_profile')
    psk = base64.b64encode(secrets.psk).decode('ascii')
    password = base64.b64encode(secrets.password.encode('utf-8')).decode('ascii')
    local_identity = f'            id = {local_id}\n' if local_id else ''
    return (
        'connections {\n    forti-full {\n        version = 2\n'
        f'        remote_addrs = {gateway}\n        remote_port = {profile.gateway_port}\n'
        f'        vips = 0.0.0.0\n        proposals = {ike}\n'
        '        remote {\n            auth = psk\n            id = %any\n        }\n'
        '        local {\n            round = 2\n            auth = eap\n'
        f'{local_identity}            eap_id = {username}\n        }}\n'
        '        children {\n            vpn {\n                local_ts = dynamic\n'
        f'                remote_ts = {",".join(networks)}\n                esp_proposals = {esp}\n'
        '                start_action = none\n            }\n        }\n    }\n}\n'
        'secrets {\n    ike-vpn {\n'
        f'        secret = 0s{psk}\n    }}\n    eap-vpn {{\n        id = {username}\n'
        f'        secret = 0s{password}\n    }}\n}}\n'
    )
