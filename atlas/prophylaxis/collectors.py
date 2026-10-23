"""Explicit collector capabilities and check-to-collector planning."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from atlas.prophylaxis.models import (
    CheckId,
    CollectorId,
    CPUUtilizationValues,
    DeviceFactsValues,
    DiagnosticValues,
    OSVersionValues,
    RawCollectorResult,
    UptimeValues,
    SystemMetricValues,
)
from atlas.prophylaxis.parsers import (
    normalize_command_cpu,
    normalize_cpu,
    normalize_device_facts,
    normalize_environment_health,
    normalize_eol_status,
    normalize_fan_health,
    normalize_firmware_recommendation,
    normalize_ha_status,
    normalize_interface_errors,
    normalize_interface_state,
    normalize_interface_utilization,
    normalize_license_state,
    normalize_memory,
    normalize_ntp_status,
    normalize_os_version,
    normalize_pki_certificates,
    normalize_recent_logs,
    normalize_routing_neighbors,
    normalize_routing_table,
    normalize_temperature,
    normalize_uptime,
    normalize_snmp_cpu,
    normalize_snmp_fans,
    normalize_snmp_memory,
    normalize_snmp_temperature,
)
from atlas.prophylaxis.platforms import AnsiblePlatformProfile


NormalizedValues = (
    CPUUtilizationValues | DeviceFactsValues | OSVersionValues | UptimeValues
    | SystemMetricValues | DiagnosticValues
)
Normalizer = Callable[[RawCollectorResult], NormalizedValues]


@dataclass(frozen=True)
class CollectorSpec:
    id: CollectorId
    normalizers: Mapping[CheckId, Normalizer]

    @property
    def checks(self) -> frozenset[CheckId]:
        return frozenset(self.normalizers)

    def normalize(
        self,
        check_id: CheckId,
        raw: RawCollectorResult,
    ) -> NormalizedValues:
        try:
            normalizer = self.normalizers[check_id]
        except KeyError:
            raise ValueError("check is not provided by collector") from None
        return normalizer(raw)


@dataclass(frozen=True)
class CollectorTask:
    collector: CollectorSpec
    checks: tuple[CheckId, ...]


@dataclass(frozen=True)
class CollectionPlan:
    tasks: tuple[CollectorTask, ...]
    unsupported_checks: tuple[CheckId, ...]


CPU_COLLECTOR = CollectorSpec(
    id=CollectorId.CPU_USAGE,
    normalizers={CheckId.CPU_UTILIZATION: normalize_cpu},
)

DEVICE_FACTS_COLLECTOR = CollectorSpec(
    id=CollectorId.DEVICE_FACTS,
    normalizers={
        CheckId.DEVICE_FACTS: normalize_device_facts,
        CheckId.OS_VERSION: normalize_os_version,
        CheckId.UPTIME: normalize_uptime,
    },
)

IOS_IDENTITY_COLLECTOR = CollectorSpec(
    id=CollectorId.IOS_IDENTITY,
    normalizers={
        CheckId.DEVICE_FACTS: normalize_device_facts,
        CheckId.OS_VERSION: normalize_os_version,
        CheckId.UPTIME: normalize_uptime,
    },
)

IOS_RESOURCES_COLLECTOR = CollectorSpec(
    id=CollectorId.IOS_RESOURCES,
    normalizers={
        CheckId.CPU_UTILIZATION: normalize_command_cpu,
        CheckId.MEMORY_UTILIZATION: normalize_memory,
        CheckId.ENVIRONMENT_HEALTH: normalize_environment_health,
        CheckId.CPU_TEMPERATURE: normalize_temperature,
        CheckId.FAN_HEALTH: normalize_fan_health,
    },
)

IOS_INTERFACES_COLLECTOR = CollectorSpec(
    id=CollectorId.IOS_INTERFACES,
    normalizers={
        CheckId.INTERFACE_STATE: normalize_interface_state,
        CheckId.INTERFACE_UTILIZATION: normalize_interface_utilization,
        CheckId.INTERFACE_ERROR_COUNTERS: normalize_interface_errors,
    },
)

IOS_COMPLIANCE_COLLECTOR = CollectorSpec(
    id=CollectorId.IOS_COMPLIANCE,
    normalizers={
        CheckId.LICENSE_STATE: normalize_license_state,
        CheckId.PKI_CERTIFICATES: normalize_pki_certificates,
    },
)

IOS_OPERATIONS_COLLECTOR = CollectorSpec(
    id=CollectorId.IOS_OPERATIONS,
    normalizers={
        CheckId.NTP_SYNC: normalize_ntp_status,
        CheckId.RECENT_LOG_EVENTS: normalize_recent_logs,
        CheckId.ROUTING_TABLE: normalize_routing_table,
        CheckId.ROUTING_PROTOCOL_NEIGHBORS: normalize_routing_neighbors,
        CheckId.HA_STATUS: normalize_ha_status,
    },
)

CBS_RESOURCES_COLLECTOR = CollectorSpec(
    id=CollectorId.CBS_RESOURCES,
    normalizers={
        CheckId.CPU_UTILIZATION: normalize_command_cpu,
        CheckId.MEMORY_UTILIZATION: normalize_memory,
        CheckId.ENVIRONMENT_HEALTH: normalize_environment_health,
        CheckId.CPU_TEMPERATURE: normalize_temperature,
        CheckId.FAN_HEALTH: normalize_fan_health,
    },
)

CBS_INTERFACES_COLLECTOR = CollectorSpec(
    id=CollectorId.CBS_INTERFACES,
    normalizers={
        CheckId.INTERFACE_STATE: normalize_interface_state,
        CheckId.INTERFACE_UTILIZATION: normalize_interface_utilization,
        CheckId.INTERFACE_ERROR_COUNTERS: normalize_interface_errors,
    },
)

CBS_COMPLIANCE_COLLECTOR = CollectorSpec(
    id=CollectorId.CBS_COMPLIANCE,
    normalizers={
        CheckId.LICENSE_STATE: normalize_license_state,
        CheckId.PKI_CERTIFICATES: normalize_pki_certificates,
    },
)

CBS_OPERATIONS_COLLECTOR = CollectorSpec(
    id=CollectorId.CBS_OPERATIONS,
    normalizers={
        CheckId.NTP_SYNC: normalize_ntp_status,
        CheckId.RECENT_LOG_EVENTS: normalize_recent_logs,
        CheckId.ROUTING_TABLE: normalize_routing_table,
        CheckId.ROUTING_PROTOCOL_NEIGHBORS: normalize_routing_neighbors,
        CheckId.HA_STATUS: normalize_ha_status,
    },
)

FORTIOS_RESOURCES_COLLECTOR = CollectorSpec(
    id=CollectorId.FORTIOS_RESOURCES,
    normalizers={
        CheckId.MEMORY_UTILIZATION: normalize_memory,
        CheckId.ENVIRONMENT_HEALTH: normalize_environment_health,
        CheckId.CPU_TEMPERATURE: normalize_temperature,
        CheckId.FAN_HEALTH: normalize_fan_health,
    },
)

FORTIOS_INTERFACES_COLLECTOR = CollectorSpec(
    id=CollectorId.FORTIOS_INTERFACES,
    normalizers={
        CheckId.INTERFACE_STATE: normalize_interface_state,
        CheckId.INTERFACE_UTILIZATION: normalize_interface_utilization,
        CheckId.INTERFACE_ERROR_COUNTERS: normalize_interface_errors,
    },
)

FORTIOS_COMPLIANCE_COLLECTOR = CollectorSpec(
    id=CollectorId.FORTIOS_COMPLIANCE,
    normalizers={
        CheckId.LICENSE_STATE: normalize_license_state,
        CheckId.PKI_CERTIFICATES: normalize_pki_certificates,
        CheckId.FIRMWARE_RECOMMENDATION: normalize_firmware_recommendation,
        CheckId.EOL_STATUS: normalize_eol_status,
    },
)

FORTIOS_OPERATIONS_COLLECTOR = CollectorSpec(
    id=CollectorId.FORTIOS_OPERATIONS,
    normalizers={
        CheckId.NTP_SYNC: normalize_ntp_status,
        CheckId.RECENT_LOG_EVENTS: normalize_recent_logs,
        CheckId.ROUTING_TABLE: normalize_routing_table,
        CheckId.ROUTING_PROTOCOL_NEIGHBORS: normalize_routing_neighbors,
        CheckId.HA_STATUS: normalize_ha_status,
    },
)

SNMP_SYSTEM_COLLECTOR = CollectorSpec(
    id=CollectorId.SNMP_SYSTEM,
    normalizers={
        CheckId.CPU_UTILIZATION: normalize_snmp_cpu,
        CheckId.MEMORY_UTILIZATION: normalize_snmp_memory,
        CheckId.CPU_TEMPERATURE: normalize_snmp_temperature,
        CheckId.FAN_HEALTH: normalize_snmp_fans,
    },
)


class CollectorRegistry:
    def __init__(self, collectors: Iterable[CollectorSpec] | None = None) -> None:
        selected = tuple(collectors or (
            CPU_COLLECTOR,
            DEVICE_FACTS_COLLECTOR,
            SNMP_SYSTEM_COLLECTOR,
            IOS_IDENTITY_COLLECTOR,
            IOS_RESOURCES_COLLECTOR,
            IOS_INTERFACES_COLLECTOR,
            IOS_COMPLIANCE_COLLECTOR,
            IOS_OPERATIONS_COLLECTOR,
            CBS_RESOURCES_COLLECTOR,
            CBS_INTERFACES_COLLECTOR,
            CBS_COMPLIANCE_COLLECTOR,
            CBS_OPERATIONS_COLLECTOR,
            FORTIOS_RESOURCES_COLLECTOR,
            FORTIOS_INTERFACES_COLLECTOR,
            FORTIOS_COMPLIANCE_COLLECTOR,
            FORTIOS_OPERATIONS_COLLECTOR,
        ))
        self._by_id: dict[CollectorId, CollectorSpec] = {}
        self._by_check: dict[CheckId, list[CollectorSpec]] = {}
        for collector in selected:
            if collector.id in self._by_id:
                raise ValueError(f"duplicate collector: {collector.id}")
            self._by_id[collector.id] = collector
            for check_id in collector.checks:
                self._by_check.setdefault(check_id, []).append(collector)

    def resolve_check(
        self,
        check_id: CheckId,
        profile: AnsiblePlatformProfile | None = None,
    ) -> CollectorSpec:
        try:
            candidates = self._by_check[check_id]
        except KeyError:
            raise ValueError("check has no registered collector") from None
        if profile is None:
            return candidates[0]
        preferred = profile.preferred_collector(check_id)
        if preferred is not None:
            for collector in candidates:
                if (
                    collector.id == preferred
                    and profile.supports_collector(collector.id)
                ):
                    return collector
        for collector in candidates:
            if profile.supports_collector(collector.id):
                return collector
        raise ValueError("check has no supported collector")

    def plan(
        self,
        check_ids: Iterable[CheckId],
        profile: AnsiblePlatformProfile,
    ) -> CollectionPlan:
        grouped: dict[CollectorId, list[CheckId]] = {}
        specs: dict[CollectorId, CollectorSpec] = {}
        unsupported: list[CheckId] = []
        for check_id in dict.fromkeys(check_ids):
            try:
                collector = self.resolve_check(check_id, profile)
            except ValueError:
                unsupported.append(check_id)
                continue
            if not profile.supports_collector(collector.id):
                unsupported.append(check_id)
                continue
            specs[collector.id] = collector
            grouped.setdefault(collector.id, []).append(check_id)
        tasks = tuple(
            CollectorTask(specs[collector_id], tuple(checks))
            for collector_id, checks in grouped.items()
        )
        return CollectionPlan(tasks, tuple(unsupported))


__all__ = [
    "CPU_COLLECTOR",
    "CBS_COMPLIANCE_COLLECTOR",
    "CBS_INTERFACES_COLLECTOR",
    "CBS_OPERATIONS_COLLECTOR",
    "CBS_RESOURCES_COLLECTOR",
    "DEVICE_FACTS_COLLECTOR",
    "FORTIOS_COMPLIANCE_COLLECTOR",
    "FORTIOS_INTERFACES_COLLECTOR",
    "FORTIOS_OPERATIONS_COLLECTOR",
    "FORTIOS_RESOURCES_COLLECTOR",
    "IOS_IDENTITY_COLLECTOR",
    "IOS_COMPLIANCE_COLLECTOR",
    "IOS_INTERFACES_COLLECTOR",
    "IOS_OPERATIONS_COLLECTOR",
    "IOS_RESOURCES_COLLECTOR",
    "SNMP_SYSTEM_COLLECTOR",
    "CollectionPlan",
    "CollectorRegistry",
    "CollectorSpec",
    "CollectorTask",
]
