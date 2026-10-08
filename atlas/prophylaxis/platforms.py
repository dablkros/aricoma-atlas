"""Explicit NetBox-platform to Ansible execution profiles."""

from dataclasses import dataclass
from typing import Literal

from atlas.prophylaxis.models import CheckId


AuthenticationType = Literal["ssh", "api_token"]


@dataclass(frozen=True)
class AnsiblePlatformProfile:
    slug: str
    transport: Literal["ssh", "https"]
    default_port: int
    netbox_service_name: str | None
    ansible_connection: str
    ansible_network_os: str
    authentication: AuthenticationType
    collection: str
    module_family: str
    supported_checks: frozenset[CheckId]

    def supports(self, check_id: CheckId) -> bool:
        return check_id in self.supported_checks


CPU_ONLY = frozenset({CheckId.CPU_UTILIZATION})

ANSIBLE_PLATFORMS = {
    "cisco-ios": AnsiblePlatformProfile(
        slug="cisco-ios",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="cisco.ios.ios",
        authentication="ssh",
        collection="cisco.ios",
        module_family="cisco.ios.ios_command",
        supported_checks=CPU_ONLY,
    ),
    "cisco-ios-xe": AnsiblePlatformProfile(
        slug="cisco-ios-xe",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="cisco.ios.ios",
        authentication="ssh",
        collection="cisco.ios",
        module_family="cisco.ios.ios_command",
        supported_checks=CPU_ONLY,
    ),
    "cisco-cbs": AnsiblePlatformProfile(
        slug="cisco-cbs",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="community.ciscosmb.ciscosmb",
        authentication="ssh",
        collection="community.ciscosmb",
        module_family="community.ciscosmb.command",
        supported_checks=CPU_ONLY,
    ),
    "fortios": AnsiblePlatformProfile(
        slug="fortios",
        transport="https",
        default_port=443,
        netbox_service_name="fortios-api",
        ansible_connection="ansible.netcommon.httpapi",
        ansible_network_os="fortinet.fortios.fortios",
        authentication="api_token",
        collection="fortinet.fortios",
        module_family="fortinet.fortios.fortios_monitor_fact",
        supported_checks=CPU_ONLY,
    ),
}


class AnsiblePlatformRegistry:
    def __init__(self, profiles=None) -> None:
        self._profiles = dict(profiles or ANSIBLE_PLATFORMS)

    def resolve(self, platform: str) -> AnsiblePlatformProfile:
        from atlas.prophylaxis.errors import UnsupportedPlatformError

        try:
            return self._profiles[platform]
        except (KeyError, TypeError):
            raise UnsupportedPlatformError() from None


__all__ = [
    "ANSIBLE_PLATFORMS",
    "AnsiblePlatformProfile",
    "AnsiblePlatformRegistry",
    "AuthenticationType",
]
