"""Explicit collector capabilities and check-to-collector planning."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from atlas.prophylaxis.models import (
    CheckId,
    CollectorId,
    CPUUtilizationValues,
    DeviceFactsValues,
    OSVersionValues,
    RawCollectorResult,
    UptimeValues,
    SystemMetricValues,
)
from atlas.prophylaxis.parsers import (
    normalize_cpu,
    normalize_device_facts,
    normalize_os_version,
    normalize_uptime,
    normalize_snmp_cpu,
    normalize_snmp_fans,
    normalize_snmp_memory,
    normalize_snmp_temperature,
)
from atlas.prophylaxis.platforms import AnsiblePlatformProfile


NormalizedValues = (
    CPUUtilizationValues | DeviceFactsValues | OSVersionValues | UptimeValues
    | SystemMetricValues
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
                if collector.id == preferred and profile.supports_collector(collector.id):
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
    "DEVICE_FACTS_COLLECTOR",
    "SNMP_SYSTEM_COLLECTOR",
    "CollectionPlan",
    "CollectorRegistry",
    "CollectorSpec",
    "CollectorTask",
]
