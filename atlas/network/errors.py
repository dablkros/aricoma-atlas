"""Secret-free failures exposed by the network collection layer."""


class NetworkCollectionError(RuntimeError):
    """Expected collection failure represented by a stable reason code."""

    code = "collection_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class UnsupportedPlatformError(NetworkCollectionError):
    code = "unsupported_platform"


class HostKeyUnknownError(NetworkCollectionError):
    code = "host_key_unknown"


class HostKeyMismatchError(NetworkCollectionError):
    code = "host_key_mismatch"


class ConnectionTimeoutError(NetworkCollectionError):
    code = "connection_timeout"


class AuthenticationFailedError(NetworkCollectionError):
    code = "authentication_failed"


class ConnectionFailedError(NetworkCollectionError):
    code = "connection_failed"


class CommandFailedError(NetworkCollectionError):
    code = "command_failed"


class ParseFailedError(NetworkCollectionError):
    code = "parse_failed"


__all__ = [
    "AuthenticationFailedError",
    "CommandFailedError",
    "ConnectionFailedError",
    "ConnectionTimeoutError",
    "HostKeyMismatchError",
    "HostKeyUnknownError",
    "NetworkCollectionError",
    "ParseFailedError",
    "UnsupportedPlatformError",
]
