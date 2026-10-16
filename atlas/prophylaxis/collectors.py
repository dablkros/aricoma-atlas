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
)
from atlas.prophylaxis.parsers import (
    normalize_cpu,
    normalize_device_facts,
    normalize_os_version,
)
from atlas.prophylaxis.platforms import AnsiblePlatformProfile


NormalizedValues = CPUUtilizationValues | DeviceFactsValues | OSVersionValues
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
    },
)


class CollectorRegistry:
    def __init__(self, collectors: Iterable[CollectorSpec] | None = None) -> None:
        selected = tuple(collectors or (CPU_COLLECTOR, DEVICE_FACTS_COLLECTOR))
        self._by_id: dict[CollectorId, CollectorSpec] = {}
        self._by_check: dict[CheckId, CollectorSpec] = {}
        for collector in selected:
            if collector.id in self._by_id:
                raise ValueError(f"duplicate collector: {collector.id}")
            self._by_id[collector.id] = collector
            for check_id in collector.checks:
                if check_id in self._by_check:
                    raise ValueError(f"duplicate check provider: {check_id}")
                self._by_check[check_id] = collector

    def resolve_check(self, check_id: CheckId) -> CollectorSpec:
        try:
            return self._by_check[check_id]
        except KeyError:
            raise ValueError("check has no registered collector") from None

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
                collector = self.resolve_check(check_id)
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
    "CollectionPlan",
    "CollectorRegistry",
    "CollectorSpec",
    "CollectorTask",
]
