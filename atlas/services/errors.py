"""Safe dependency error contract shared by Atlas service boundaries."""

from typing import Literal


DependencyReason = Literal[
    "connection_failed",
    "authentication_failed",
    "sealed",
    "not_initialized",
    "secret_not_found",
    "invalid_response",
    "unavailable",
]

DEPENDENCY_REASONS = {
    "connection_failed",
    "authentication_failed",
    "sealed",
    "not_initialized",
    "secret_not_found",
    "invalid_response",
    "unavailable",
}


class DependencyError(RuntimeError):
    """Expected integration failure containing only a safe reason code."""

    def __init__(self, reason: DependencyReason) -> None:
        if reason not in DEPENDENCY_REASONS:
            raise ValueError("Unsupported dependency failure reason")
        self.reason = reason
        super().__init__(reason)
