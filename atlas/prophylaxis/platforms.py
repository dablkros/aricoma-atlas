"""Explicit NetBox-platform to Ansible execution profiles."""

from dataclasses import dataclass
from typing import Literal

from atlas.prophylaxis.models import CollectorId


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
    supported_collectors: frozenset[CollectorId]
    legacy_kex_algorithms: tuple[str, ...] = ()
    legacy_hostkey_algorithms: tuple[str, ...] = ()
    legacy_ciphers: tuple[str, ...] = ()
    telnet_fallback_service_name: str | None = None
    telnet_fallback_default_port: int = 23

    def supports_collector(self, collector_id: CollectorId) -> bool:
        return collector_id in self.supported_collectors


CPU_AND_FACTS = frozenset({CollectorId.CPU_USAGE, CollectorId.DEVICE_FACTS})
FACTS_ONLY = frozenset({CollectorId.DEVICE_FACTS})

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
        supported_collectors=CPU_AND_FACTS,
        legacy_kex_algorithms=("diffie-hellman-group1-sha1",),
        legacy_hostkey_algorithms=(
            "ssh-ed25519",
            "ecdsa-sha2-nistp521",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp256",
            "rsa-sha2-512",
            "rsa-sha2-256",
            "ssh-rsa",
        ),
        legacy_ciphers=("aes128-cbc",),
        telnet_fallback_service_name="telnet",
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
        supported_collectors=CPU_AND_FACTS,
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
        supported_collectors=CPU_AND_FACTS,
    ),
    "cisco-cbs250-350": AnsiblePlatformProfile(
        slug="cisco-cbs250-350",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="community.ciscosmb.ciscosmb",
        authentication="ssh",
        collection="community.ciscosmb",
        module_family="community.ciscosmb.command",
        supported_collectors=CPU_AND_FACTS,
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
        supported_collectors=CPU_AND_FACTS,
    ),
    "aruba-aos-cx": AnsiblePlatformProfile(
        slug="aruba-aos-cx",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="arubanetworks.aoscx.aoscx",
        authentication="ssh",
        collection="arubanetworks.aoscx",
        module_family="arubanetworks.aoscx.aoscx_command",
        supported_collectors=FACTS_ONLY,
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
