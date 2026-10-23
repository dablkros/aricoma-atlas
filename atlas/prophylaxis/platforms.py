"""Explicit NetBox-platform to Ansible execution profiles."""

from dataclasses import dataclass
from typing import Literal

from atlas.prophylaxis.models import CheckId, CollectorId


AuthenticationType = Literal["ssh", "api_token"]


@dataclass(frozen=True)
class AnsiblePlatformProfile:
    slug: str
    transport: Literal["ssh", "https"]
    default_port: int
    netbox_service_name: str | None
    ansible_connection: str
    ansible_network_os: str | None
    authentication: AuthenticationType
    collection: str
    module_family: str
    supported_collectors: frozenset[CollectorId]
    preferred_collectors: tuple[tuple[CheckId, CollectorId], ...] = ()
    legacy_kex_algorithms: tuple[str, ...] = ()
    legacy_hostkey_algorithms: tuple[str, ...] = ()
    legacy_ciphers: tuple[str, ...] = ()
    legacy_macs: tuple[str, ...] = ()
    telnet_fallback_service_name: str | None = None
    telnet_fallback_default_port: int = 23

    def supports_collector(self, collector_id: CollectorId) -> bool:
        return collector_id in self.supported_collectors

    def preferred_collector(self, check_id: CheckId) -> CollectorId | None:
        return dict(self.preferred_collectors).get(check_id)


CPU_AND_FACTS = frozenset({CollectorId.CPU_USAGE, CollectorId.DEVICE_FACTS})
FORTIOS_SSH_PROFILE = AnsiblePlatformProfile(
    slug="fortios",
    transport="ssh",
    default_port=22,
    netbox_service_name=None,
    # FortiOS CLI is an interactive appliance shell. Atlas executes the fixed
    # read-only command allow-list through its local Paramiko module.
    ansible_connection="local",
    ansible_network_os=None,
    authentication="ssh",
    collection="atlas.builtin",
    module_family="atlas_fortios_cli",
    supported_collectors=CPU_AND_FACTS,
)
IOS_IMPLEMENTED_COLLECTORS = frozenset({
    CollectorId.IOS_IDENTITY,
    CollectorId.IOS_RESOURCES,
    CollectorId.IOS_INTERFACES,
    CollectorId.IOS_COMPLIANCE,
    CollectorId.IOS_OPERATIONS,
    # Keep the previous IDs executable for compatibility with direct backend
    # callers. The planner explicitly prefers the thematic IDs below.
    CollectorId.CPU_USAGE,
    CollectorId.DEVICE_FACTS,
})
IOS_COLLECTOR_PREFERENCES = (
    (CheckId.DEVICE_FACTS, CollectorId.IOS_IDENTITY),
    (CheckId.OS_VERSION, CollectorId.IOS_IDENTITY),
    (CheckId.UPTIME, CollectorId.IOS_IDENTITY),
    (CheckId.CPU_UTILIZATION, CollectorId.IOS_RESOURCES),
    (CheckId.MEMORY_UTILIZATION, CollectorId.IOS_RESOURCES),
    (CheckId.ENVIRONMENT_HEALTH, CollectorId.IOS_RESOURCES),
    (CheckId.CPU_TEMPERATURE, CollectorId.IOS_RESOURCES),
    (CheckId.FAN_HEALTH, CollectorId.IOS_RESOURCES),
    (CheckId.INTERFACE_STATE, CollectorId.IOS_INTERFACES),
    (CheckId.INTERFACE_UTILIZATION, CollectorId.IOS_INTERFACES),
    (CheckId.INTERFACE_ERROR_COUNTERS, CollectorId.IOS_INTERFACES),
    (CheckId.LICENSE_STATE, CollectorId.IOS_COMPLIANCE),
    (CheckId.PKI_CERTIFICATES, CollectorId.IOS_COMPLIANCE),
    (CheckId.NTP_SYNC, CollectorId.IOS_OPERATIONS),
    (CheckId.RECENT_LOG_EVENTS, CollectorId.IOS_OPERATIONS),
    (CheckId.ROUTING_TABLE, CollectorId.IOS_OPERATIONS),
    (CheckId.ROUTING_PROTOCOL_NEIGHBORS, CollectorId.IOS_OPERATIONS),
    (CheckId.HA_STATUS, CollectorId.IOS_OPERATIONS),
)
FACTS_ONLY = frozenset({CollectorId.DEVICE_FACTS})
CBS_COLLECTORS = frozenset({
    CollectorId.CPU_USAGE,
    CollectorId.DEVICE_FACTS,
    CollectorId.SNMP_SYSTEM,
    CollectorId.CBS_RESOURCES,
    CollectorId.CBS_INTERFACES,
    CollectorId.CBS_COMPLIANCE,
    CollectorId.CBS_OPERATIONS,
})
ARUBA_CX_COLLECTORS = frozenset({CollectorId.DEVICE_FACTS, CollectorId.SNMP_SYSTEM})
CBS_COLLECTOR_PREFERENCES = (
    (CheckId.CPU_UTILIZATION, CollectorId.SNMP_SYSTEM),
    (CheckId.CPU_TEMPERATURE, CollectorId.SNMP_SYSTEM),
    (CheckId.FAN_HEALTH, CollectorId.SNMP_SYSTEM),
    (CheckId.MEMORY_UTILIZATION, CollectorId.CBS_RESOURCES),
    (CheckId.ENVIRONMENT_HEALTH, CollectorId.CBS_RESOURCES),
    (CheckId.INTERFACE_STATE, CollectorId.CBS_INTERFACES),
    (CheckId.INTERFACE_UTILIZATION, CollectorId.CBS_INTERFACES),
    (CheckId.INTERFACE_ERROR_COUNTERS, CollectorId.CBS_INTERFACES),
    (CheckId.LICENSE_STATE, CollectorId.CBS_COMPLIANCE),
    (CheckId.PKI_CERTIFICATES, CollectorId.CBS_COMPLIANCE),
    (CheckId.NTP_SYNC, CollectorId.CBS_OPERATIONS),
    (CheckId.RECENT_LOG_EVENTS, CollectorId.CBS_OPERATIONS),
    (CheckId.ROUTING_TABLE, CollectorId.CBS_OPERATIONS),
    (CheckId.ROUTING_PROTOCOL_NEIGHBORS, CollectorId.CBS_OPERATIONS),
    (CheckId.HA_STATUS, CollectorId.CBS_OPERATIONS),
)

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
        supported_collectors=IOS_IMPLEMENTED_COLLECTORS,
        preferred_collectors=IOS_COLLECTOR_PREFERENCES,
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
        legacy_macs=("hmac-sha1",),
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
        supported_collectors=IOS_IMPLEMENTED_COLLECTORS,
        preferred_collectors=IOS_COLLECTOR_PREFERENCES,
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
        supported_collectors=CBS_COLLECTORS,
        preferred_collectors=CBS_COLLECTOR_PREFERENCES,
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
        supported_collectors=CBS_COLLECTORS,
        preferred_collectors=CBS_COLLECTOR_PREFERENCES,
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
    "checkpoint-gaia": AnsiblePlatformProfile(
        slug="checkpoint-gaia",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        # Gaia Clish is an interactive restricted shell.  The role therefore
        # executes the allow-listed collector locally through atlas_gaia_clish
        # instead of pretending Gaia has an ansible.netcommon cliconf plugin.
        ansible_connection="local",
        ansible_network_os=None,
        authentication="ssh",
        collection="atlas.builtin",
        module_family="atlas_gaia_clish",
        supported_collectors=FACTS_ONLY,
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
        supported_collectors=ARUBA_CX_COLLECTORS,
    ),
    "aruba-aos-switch": AnsiblePlatformProfile(
        slug="aruba-aos-switch",
        transport="ssh",
        default_port=22,
        netbox_service_name=None,
        ansible_connection="ansible.netcommon.network_cli",
        ansible_network_os="arubanetworks.aos_switch.arubaoss",
        authentication="ssh",
        collection="arubanetworks.aos_switch",
        module_family="arubanetworks.aos_switch.arubaoss_command",
        supported_collectors=frozenset(),
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
    "FORTIOS_SSH_PROFILE",
    "AnsiblePlatformProfile",
    "AnsiblePlatformRegistry",
    "AuthenticationType",
]
