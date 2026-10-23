"""Cisco Support API client contracts owned by Atlas."""

from atlas.integrations.cisco_support.client import (
    CiscoAccessToken,
    CiscoEoxRecord,
    CiscoEoxResult,
    CiscoSupportAPIError,
    CiscoSupportClient,
    CiscoSupportCredentials,
    CiscoSoftwareSuggestion,
)


__all__ = [
    "CiscoAccessToken",
    "CiscoEoxRecord",
    "CiscoEoxResult",
    "CiscoSupportAPIError",
    "CiscoSupportClient",
    "CiscoSupportCredentials",
    "CiscoSoftwareSuggestion",
]
