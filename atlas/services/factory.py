"""Factory for the dependency services owned by one Atlas API process."""

from dataclasses import dataclass

from atlas.config import Settings
from atlas.integrations.cisco_support import CiscoSupportClient
from atlas.monitoring.service import MonitoringService
from atlas.monitoring.secrets import MonitoringSecrets
from atlas.openbao_client import OpenBaoClient
from atlas.prophylaxis.ansible_backend import AnsibleBackend
from atlas.prophylaxis.runs import DeviceFactsRunService
from atlas.prophylaxis.run_store import FileRunStore
from atlas.prophylaxis.service import ProphylaxisService
from atlas.prophylaxis.store import SQLiteResultStore
from atlas.services.netbox import NetBoxService
from atlas.services.cisco_support import CiscoSupportService
from atlas.services.credential_onboarding import CredentialOnboardingService
from atlas.services.openbao import OpenBaoService
from atlas.services.oxidized import OxidizedService
from atlas.services.oxidized_operations import OxidizedOperationsService
from atlas.services.oxidized_sync import FileInventoryStore, OxidizedSyncService
from atlas.services.platform_status import PlatformStatusService
from atlas.services.zabbix import ZabbixMonitoringService, ZabbixService
from atlas.services.zabbix_sync import ZabbixSyncService
from atlas.vpn.service import VPNRegistryService
from atlas.vpn.store import SQLiteVPNConnectionStore
from atlas.vpn.import_service import VPNProfileImportService


@dataclass(frozen=True)
class DependencyServices:
    openbao: OpenBaoService
    netbox: NetBoxService
    oxidized: OxidizedService
    oxidized_sync: OxidizedSyncService
    oxidized_operations: OxidizedOperationsService
    platform_status: PlatformStatusService
    prophylaxis: ProphylaxisService
    prophylaxis_runs: DeviceFactsRunService
    zabbix: ZabbixService
    zabbix_sync: ZabbixSyncService
    zabbix_monitoring: ZabbixMonitoringService
    vpn: VPNRegistryService
    monitoring: MonitoringService = None
    cisco_support: CiscoSupportService = None
    credentials: CredentialOnboardingService = None
    vpn_import: VPNProfileImportService = None


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
    provisioner = OpenBaoService(
        OpenBaoClient(str(settings.openbao_url), timeout=timeout), settings.monitoring_identity_file)
    credential_writer = OpenBaoService(
        OpenBaoClient(str(settings.openbao_url), timeout=timeout),
        settings.credential_writer_identity_file,
    )
    vpn_writer = OpenBaoService(
        OpenBaoClient(str(settings.openbao_url), timeout=timeout),
        settings.vpn_profile_writer_identity_file,
    )
    vpn_registry = VPNRegistryService(
        SQLiteVPNConnectionStore(settings.vpn_registry_file), netbox, openbao,
    )
    zabbix_sync = ZabbixSyncService(
        netbox,
        openbao,
        zabbix,
        monitoring_secrets=MonitoringSecrets(provisioner),
    )
    ansible_backend = AnsibleBackend(
        settings.ansible_project_dir,
        known_hosts_file=settings.ssh_known_hosts_file,
        strict_host_keys=settings.ssh_strict_host_keys,
        connect_timeout=settings.ssh_connect_timeout,
        command_timeout=settings.ssh_command_timeout,
        job_timeout=settings.ansible_job_timeout,
        fortios_validate_certs=settings.fortios_validate_certs,
    )
    monitoring = MonitoringService(
        netbox,
        provisioner,
        zabbix,
        settings.monitoring_config_file,
        settings.monitoring_jobs_directory,
        credential_source=openbao,
        zabbix_sync=zabbix_sync,
        ansible_backend=ansible_backend,
    )
    inventory_store = FileInventoryStore(settings.oxidized_inventory_file)
    zabbix_monitoring = ZabbixMonitoringService(zabbix)
    cisco_support = CiscoSupportService(
        openbao,
        CiscoSupportClient(
            connect_timeout=settings.http_connect_timeout,
            read_timeout=settings.http_read_timeout,
        ),
    )
    return DependencyServices(
        monitoring=monitoring,
        cisco_support=cisco_support,
        credentials=CredentialOnboardingService(
            netbox,
            credential_writer,
            credential_writer,
        ),
        vpn=vpn_registry,
        vpn_import=VPNProfileImportService(vpn_registry, vpn_writer),
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
        platform_status=PlatformStatusService(
            openbao,
            netbox,
            oxidized,
            zabbix,
            oxidized_enabled=settings.oxidized_enabled,
            zabbix_enabled=settings.zabbix_enabled,
        ),
        prophylaxis=ProphylaxisService(
            netbox,
            openbao,
            backend=ansible_backend,
            result_store=SQLiteResultStore(
                settings.prophylaxis_results_file,
                settings.prophylaxis_result_retention,
            ),
        ),
        prophylaxis_runs=DeviceFactsRunService(
            netbox,
            openbao,
            backend=ansible_backend,
            store=FileRunStore(settings.prophylaxis_runs_directory),
            concurrency=settings.prophylaxis_concurrency,
            inventory_reconciliation_enabled=True,
            zabbix_monitoring=(zabbix_monitoring if settings.zabbix_enabled else None),
            oxidized=(oxidized if settings.oxidized_enabled else None),
            cisco_support=cisco_support,
        ),
        zabbix=zabbix,
        zabbix_sync=zabbix_sync,
        zabbix_monitoring=zabbix_monitoring,
    )


__all__ = ["DependencyServices", "build_dependency_services"]
