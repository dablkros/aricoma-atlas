"""Factory for the dependency services owned by one Atlas API process."""

from dataclasses import dataclass

from atlas.config import Settings
from atlas.network.base import SSHTimeouts
from atlas.openbao_client import OpenBaoClient
from atlas.prophylaxis.service import ProphylaxisService
from atlas.services.netbox import NetBoxService
from atlas.services.openbao import OpenBaoService
from atlas.services.oxidized import OxidizedService
from atlas.services.oxidized_operations import OxidizedOperationsService
from atlas.services.oxidized_sync import FileInventoryStore, OxidizedSyncService
from atlas.services.platform_status import PlatformStatusService


@dataclass(frozen=True)
class DependencyServices:
    openbao: OpenBaoService
    netbox: NetBoxService
    oxidized: OxidizedService
    oxidized_sync: OxidizedSyncService
    oxidized_operations: OxidizedOperationsService
    platform_status: PlatformStatusService
    prophylaxis: ProphylaxisService


def build_dependency_services(settings: Settings) -> DependencyServices:
    timeout = (settings.http_connect_timeout, settings.http_read_timeout)
    openbao = OpenBaoService(
        OpenBaoClient(str(settings.openbao_url), timeout=timeout),
        settings.openbao_identity_file,
    )
    netbox = NetBoxService(
        str(settings.netbox_url),
        openbao,
        settings.http_connect_timeout,
        settings.http_read_timeout,
    )
    oxidized = OxidizedService(
        str(settings.oxidized_url),
        settings.http_connect_timeout,
        settings.http_read_timeout,
    )
    inventory_store = FileInventoryStore(settings.oxidized_inventory_file)
    return DependencyServices(
        openbao=openbao,
        netbox=netbox,
        oxidized=oxidized,
        oxidized_sync=OxidizedSyncService(
            netbox,
            openbao,
            oxidized,
            inventory_store,
        ),
        oxidized_operations=OxidizedOperationsService(
            netbox,
            oxidized,
            inventory_store,
        ),
        platform_status=PlatformStatusService(openbao, netbox, oxidized),
        prophylaxis=ProphylaxisService(
            netbox,
            openbao,
            known_hosts_file=settings.ssh_known_hosts_file,
            strict_host_keys=settings.ssh_strict_host_keys,
            timeouts=SSHTimeouts(
                settings.ssh_connect_timeout,
                settings.ssh_auth_timeout,
                settings.ssh_command_timeout,
            ),
        ),
    )


__all__ = ["DependencyServices", "build_dependency_services"]
