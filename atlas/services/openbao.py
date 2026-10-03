"""Stable service-level imports for the existing OpenBao client."""

from atlas.openbao_client import OpenBaoClient, OpenBaoError, OpenBaoNotFound


__all__ = ["OpenBaoClient", "OpenBaoError", "OpenBaoNotFound"]
