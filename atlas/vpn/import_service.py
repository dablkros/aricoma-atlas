"""Plan and import FortiClient VPN profiles with secret-safe CAS writes."""

import base64
import binascii
from dataclasses import asdict
import plistlib

from defusedxml import ElementTree as SafeET

from atlas.services.errors import DependencyError
from atlas.vpn.forticlient_profile import (
    FortiClientProfileError, decode_macos_enc, decode_windows_encx,
    parse_forticlient_profile,
)
from atlas.vpn.import_models import ProfileImportRequest, ProfileInspectRequest
from atlas.vpn.models import VPNConnectionCreate
from atlas.vpn.service import VPNRegistryError
from atlas.vpn.strongswan_config import VPNSecrets, validate_profile

MAX_PROFILE_BYTES = 2 * 1024 * 1024


class VPNImportError(Exception):
    def __init__(self, code: str, status_code: int = 422):
        super().__init__(code)
        self.code, self.status_code = code, status_code


def profile_bytes(body: ProfileInspectRequest) -> bytes:
    encoded = body.profile_base64.get_secret_value()
    if len(encoded) > ((MAX_PROFILE_BYTES + 2) // 3) * 4:
        raise VPNImportError('vpn_profile_too_large')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise VPNImportError('vpn_profile_invalid_encoding') from None
    if not data or len(data) > MAX_PROFILE_BYTES:
        raise VPNImportError('vpn_profile_invalid_size')
    return data


def inspect_profile(body: ProfileInspectRequest) -> dict:
    data = profile_bytes(body)
    try:
        if body.format == 'macos-plist':
            if not data.startswith(b'bplist00'):
                SafeET.fromstring(data)
            profiles = plistlib.loads(data).get('Profiles')
            if not isinstance(profiles, dict):
                raise ValueError()
            names = list(profiles)
        else:
            root = SafeET.fromstring(data)
            names = [(node.findtext('name') or '').strip() for node in root.iter('connection')]
        if (not names or len(names) > 100 or len(set(names)) != len(names)
                or any(not isinstance(name, str) or not name or len(name) > 200 for name in names)):
            raise ValueError()
        return {'profiles': sorted(names)}
    except Exception:
        raise VPNImportError('vpn_profile_invalid') from None


class VPNProfileImportService:
    def __init__(self, registry, writer):
        self.registry = registry
        self.writer = writer

    def options(self):
        try:
            tenants = self.registry.netbox.get_all('/api/tenancy/tenants/')
            vrfs = self.registry.netbox.get_all('/api/ipam/vrfs/')
        except DependencyError:
            raise VPNImportError('netbox_unavailable', 503) from None
        if not isinstance(tenants, list) or not isinstance(vrfs, list):
            raise VPNImportError('netbox_unavailable', 503)
        return {
            'tenants': [{'id': x.get('id'), 'name': x.get('name')} for x in tenants
                        if isinstance(x, dict) and isinstance(x.get('id'), int) and isinstance(x.get('name'), str)],
            'vrfs': [{'id': x.get('id'), 'name': x.get('name'),
                      'tenant_id': (x.get('tenant') or {}).get('id')}
                     for x in vrfs if isinstance(x, dict) and isinstance(x.get('id'), int)
                     and isinstance(x.get('name'), str) and isinstance(x.get('tenant'), dict)
                     and isinstance(x['tenant'].get('id'), int)],
        }

    def _values(self, body: ProfileImportRequest):
        try:
            p = parse_forticlient_profile(profile_bytes(body), body.profile_name, format=body.format)
            if p.encrypted_auth_info.startswith('EncX '):
                if body.export_password is None:
                    raise VPNImportError('vpn_export_password_required')
                psk = decode_windows_encx(p.encrypted_auth_info, body.export_password.get_secret_value())
            elif p.encrypted_auth_info.startswith('Enc '):
                psk = decode_macos_enc(p.encrypted_auth_info)
            else:
                raise VPNImportError('vpn_secret_format_unsupported')
            username, password = body.username.get_secret_value(), body.password.get_secret_value()
            record = VPNConnectionCreate(
                connection_id=body.connection_id, name=body.name, tenant_id=body.tenant_id,
                vendor='fortinet', protocol='ipsec-ikev2', driver='strongswan',
                gateway_host=p.gateway, gateway_port=p.gateway_port, auth_method='psk',
                secret_ref=f'vpn/connections/{body.connection_id}',
                management_vrf_id=body.management_vrf_id, allowed_prefixes=body.allowed_prefixes,
                administrative_status='planned',
            )
            # Fail closed if the profile or credentials cannot be represented by the current renderer.
            validate_profile(p, VPNSecrets(username, password, psk))
            profile = asdict(p)
            del profile['encrypted_auth_info']
            profile['phase1_proposals'] = list(profile['phase1_proposals'])
            profile['phase2_proposals'] = list(profile['phase2_proposals'])
            secret = {'schema_version': 1, 'connection_id': record.connection_id,
                      'tenant_id': record.tenant_id, 'profile': profile,
                      'psk_base64': base64.b64encode(psk).decode('ascii'),
                      'username': username, 'password': password}
            return record, secret, p
        except FortiClientProfileError as exc:
            raise VPNImportError(exc.args[0] if exc.args else 'vpn_profile_invalid') from None
        except VPNImportError:
            raise
        except Exception:
            raise VPNImportError('vpn_profile_invalid') from None

    def plan(self, body: ProfileImportRequest):
        record, secret, profile = self._values(body)
        self.registry._validate_references(record)
        try:
            existing = self.registry.get(record.connection_id)
        except VPNRegistryError as exc:
            if exc.code != 'connection_not_found':
                raise
            existing = None
        desired = record.model_dump(mode='json')
        if existing is not None:
            current = existing.model_dump(include=set(desired), mode='json')
            if current != desired:
                raise VPNImportError('vpn_connection_conflict', 409)
        try:
            current_secret, metadata = self.writer.read_secret_with_metadata(record.secret_ref)
        except DependencyError as exc:
            if exc.reason != 'secret_not_found':
                raise VPNImportError('vpn_secret_unavailable', 503) from None
            current_secret, metadata = None, {'version': 0}
        version = metadata.get('version')
        if (isinstance(version, bool) or not isinstance(version, int)
                or (current_secret is None and version != 0)
                or (current_secret is not None and version < 1)):
            raise VPNImportError('vpn_secret_unavailable', 503)
        if current_secret is not None and current_secret != secret:
            raise VPNImportError('vpn_secret_conflict', 409)
        return {'connection_id': record.connection_id, 'tenant_id': record.tenant_id,
                'management_vrf_id': record.management_vrf_id, 'gateway': record.gateway_host,
                'port': record.gateway_port, 'network_id': profile.network_id,
                'profile_name': profile.name, 'allowed_prefixes': record.allowed_prefixes,
                'registry_action': 'unchanged' if existing else 'create',
                'secret_action': 'unchanged' if current_secret is not None else 'create',
                'secret_version': version, 'runtime_status': 'not_deployed', 'apply_allowed': True}

    def apply(self, body: ProfileImportRequest, expected_plan: dict):
        if self.plan(body) != expected_plan:
            raise VPNImportError('vpn_import_plan_stale', 409)
        record, secret, _ = self._values(body)
        if expected_plan['registry_action'] == 'create':
            self.registry.create(record)
        if expected_plan['secret_action'] == 'create':
            try:
                self.writer.write_secret(record.secret_ref, secret, cas=expected_plan['secret_version'])
            except DependencyError:
                # The planned registry record may already exist; retry is explicitly supported.
                raise VPNImportError('vpn_import_partial', 503) from None
        return {'connection_id': record.connection_id, 'tenant_id': record.tenant_id,
                'runtime_status': 'not_deployed', 'status': 'saved'}
