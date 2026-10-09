"""Factory for the dependency services owned by one Atlas API process."""

from dataclasses import dataclass

from atlas.config import Settings
from atlas.openbao_client import OpenBaoClient
from atlas.prophylaxis.ansible_backend import AnsibleBackend
from atlas.prophylaxis.service import ProphylaxisService
from atlas.prophylaxis.store import SQLiteResultStore
from atlas.services.netbox import NetBoxService
from atlas.services.openbao import OpenBaoService
from atlas.services.oxidized import OxidizedService
from atlas.services.oxidized_operations import OxidizedOperationsService
from atlas.services.oxidized_sync import FileInventoryStore, OxidizedSyncService
from atlas.services.platform_status import PlatformStatusService
from atlas.services.zabbix import ZabbixMonitoringService, ZabbixService
from atlas.services.zabbix_sync import ZabbixSyncService


@dataclass(frozen=True)
class DependencyServices:
    openbao: OpenBaoService
    netbox: NetBoxService
    oxidized: OxidizedService
    oxidized_sync: OxidizedSyncService
    oxidized_operations: OxidizedOperationsService
    platform_status: PlatformStatusService
    prophylaxis: ProphylaxisService
    zabbix: ZabbixService
    zabbix_sync: ZabbixSyncService
    zabbix_monitoring: ZabbixMonitoringService


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
    zabbix = ZabbixService(
        str(settings.zabbix_url),
        openbao,
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
        platform_status=PlatformStatusService(openbao, netbox, oxidized, zabbix),
        prophylaxis=ProphylaxisService(
            netbox,
            openbao,
            backend=AnsibleBackend(
                settings.ansible_project_dir,
                known_hosts_file=settings.ssh_known_hosts_file,
                strict_host_keys=settings.ssh_strict_host_keys,
                connect_timeout=settings.ssh_connect_timeout,
                command_timeout=settings.ssh_command_timeout,
                job_timeout=settings.ansible_job_timeout,
                fortios_validate_certs=settings.fortios_validate_certs,
            ),
            result_store=SQLiteResultStore(
                settings.prophylaxis_results_file,
                settings.prophylaxis_result_retention,
            ),
        ),
        zabbix=zabbix,
        zabbix_sync=ZabbixSyncService(netbox, openbao, zabbix),
        zabbix_monitoring=ZabbixMonitoringService(zabbix),
    )


__all__ = ["DependencyServices", "build_dependency_services"]
