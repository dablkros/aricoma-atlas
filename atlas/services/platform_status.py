"""Shared application-level dependency status for readiness and Operations UI."""

from dataclasses import dataclass
from typing import Dict

from atlas.services.errors import DependencyError


@dataclass(frozen=True)
class ComponentStatus:
    status: str
    reason: str = ""


@dataclass(frozen=True)
class PlatformStatus:
    status: str
    components: Dict[str, ComponentStatus]


class PlatformStatusService:
    """Check application dependencies without host or Docker privileges."""

    def __init__(self, openbao, netbox, oxidized, zabbix) -> None:
        self.dependencies = {
            "openbao": openbao,
            "netbox": netbox,
            "oxidized": oxidized,
            "zabbix": zabbix,
        }

    def check(self) -> PlatformStatus:
        components = {"atlas": ComponentStatus("healthy")}
        for name, service in self.dependencies.items():
            try:
                service.check_ready()
                components[name] = ComponentStatus("healthy")
            except DependencyError as exc:
                components[name] = ComponentStatus("unhealthy", exc.reason)
            except Exception:
                components[name] = ComponentStatus("unhealthy", "unavailable")

        dependency_states = [
            components[name].status for name in self.dependencies
        ]
        if all(value == "healthy" for value in dependency_states):
            overall = "healthy"
        elif all(value == "unhealthy" for value in dependency_states):
            overall = "unhealthy"
        else:
            overall = "degraded"
        return PlatformStatus(overall, components)


__all__ = [
    "ComponentStatus",
    "PlatformStatus",
    "PlatformStatusService",
]
