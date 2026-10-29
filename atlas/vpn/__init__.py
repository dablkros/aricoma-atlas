"""Persistent VPN connection registry without tunnel orchestration."""

from atlas.vpn.models import VPNConnectionCreate, VPNConnectionPatch, VPNConnectionRecord
from atlas.vpn.service import VPNRegistryService
from atlas.vpn.store import SQLiteVPNConnectionStore

__all__ = [
    "SQLiteVPNConnectionStore",
    "VPNConnectionCreate",
    "VPNConnectionPatch",
    "VPNConnectionRecord",
    "VPNRegistryService",
]
