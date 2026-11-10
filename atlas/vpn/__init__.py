"""Persistent VPN connection registry without tunnel orchestration."""

from atlas.vpn.models import (
    VPNAssignmentRequest,
    VPNConnectionCreate,
    VPNConnectionPatch,
    VPNConnectionRecord,
)
from atlas.vpn.service import VPNRegistryService
from atlas.vpn.store import SQLiteVPNConnectionStore

__all__ = [
    "SQLiteVPNConnectionStore",
    "VPNAssignmentRequest",
    "VPNConnectionCreate",
    "VPNConnectionPatch",
    "VPNConnectionRecord",
    "VPNRegistryService",
]
