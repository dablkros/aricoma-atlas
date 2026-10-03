"""Factory for the dependency services owned by one Atlas API process."""

from dataclasses import dataclass

from atlas.config import Settings
from atlas.openbao_client import OpenBaoClient
from atlas.services.netbox import NetBoxService
from atlas.services.openbao import OpenBaoService
from atlas.services.oxidized import OxidizedService


@dataclass(frozen=True)
class DependencyServices:
    openbao: OpenBaoService
    netbox: NetBoxService
    oxidized: OxidizedService


def build_dependency_services(settings: Settings) -> DependencyServices:
    timeout = (settings.http_connect_timeout, settings.http_read_timeout)
    openbao = OpenBaoService(
        OpenBaoClient(str(settings.openbao_url), timeout=timeout),
        settings.openbao_identity_file,
    )
    return DependencyServices(
        openbao=openbao,
        netbox=NetBoxService(
            str(settings.netbox_url),
            openbao,
            settings.http_connect_timeout,
            settings.http_read_timeout,
        ),
        oxidized=OxidizedService(
            str(settings.oxidized_url),
            settings.http_connect_timeout,
            settings.http_read_timeout,
        ),
    )


__all__ = ["DependencyServices", "build_dependency_services"]
