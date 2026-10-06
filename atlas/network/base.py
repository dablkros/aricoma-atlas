"""Connection context and the deliberately small network-driver contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from atlas.prophylaxis.models import CPUUtilizationValues


@dataclass(frozen=True)
class SSHTimeouts:
    connect: float
    authentication: float
    command: float


@dataclass(frozen=True)
class ConnectionContext:
    device_name: str
    host: str
    platform: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    enable_password: str | None = field(default=None, repr=False)
    strict_host_keys: bool = False
    known_hosts_file: Path = Path("/run/atlas/ssh/known_hosts")
    timeouts: SSHTimeouts = SSHTimeouts(10.0, 10.0, 20.0)


class CommandTransport(Protocol):
    def run_command(
        self,
        context: ConnectionContext,
        *,
        device_type: str,
        command: str,
        use_enable: bool = False,
    ) -> str: ...


class NetworkDriver(ABC):
    """Collect metrics from one already-resolved device context."""

    platform_device_type: str
    cpu_command: str
    use_enable = False

    def __init__(
        self,
        context: ConnectionContext,
        transport: CommandTransport,
    ) -> None:
        self.context = context
        self.transport = transport

    def get_cpu_utilization(self) -> CPUUtilizationValues:
        raw_output = self.transport.run_command(
            self.context,
            device_type=self.platform_device_type,
            command=self.cpu_command,
            use_enable=self.use_enable,
        )
        return self.parse_cpu_output(raw_output)

    @staticmethod
    @abstractmethod
    def parse_cpu_output(raw_output: str) -> CPUUtilizationValues:
        raise NotImplementedError


__all__ = [
    "CommandTransport",
    "ConnectionContext",
    "NetworkDriver",
    "SSHTimeouts",
]
