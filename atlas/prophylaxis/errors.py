"""Stable failures raised while resolving a Profylaxia device."""


class DeviceResolutionError(RuntimeError):
    code = "device_resolution_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class DeviceNotFoundError(DeviceResolutionError):
    code = "device_not_found"


class DeviceAmbiguousError(DeviceResolutionError):
    code = "device_ambiguous"


class MissingPrimaryIPError(DeviceResolutionError):
    code = "missing_primary_ip"


class MissingPlatformError(DeviceResolutionError):
    code = "missing_platform"


class MissingCredentialProfileError(DeviceResolutionError):
    code = "missing_credential_profile"


class MissingManufacturerError(DeviceResolutionError):
    code = "missing_manufacturer"


class MissingDeviceNameError(DeviceResolutionError):
    code = "missing_device_name"


class ProphylaxisDisabledError(DeviceResolutionError):
    code = "prophylaxis_disabled"


class CheckNotSelectedError(DeviceResolutionError):
    code = "check_not_selected"


class DeviceInactiveError(DeviceResolutionError):
    code = "device_inactive"


class AmbiguousServicePortError(DeviceResolutionError):
    code = "ambiguous_service_port"


class InvalidServicePortError(DeviceResolutionError):
    code = "invalid_service_port"


class ProphylaxisExecutionError(RuntimeError):
    code = "execution_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class UnsupportedPlatformError(ProphylaxisExecutionError):
    code = "unsupported_platform"


class AnsibleExecutionError(ProphylaxisExecutionError):
    code = "ansible_execution_failed"


class AnsibleContentError(ProphylaxisExecutionError):
    code = "ansible_content_error"


class HostKeyUnknownError(ProphylaxisExecutionError):
    code = "host_key_unknown"


class HostKeyMismatchError(ProphylaxisExecutionError):
    code = "host_key_mismatch"


class ConnectionTimeoutError(ProphylaxisExecutionError):
    code = "connection_timeout"


class AuthenticationFailedError(ProphylaxisExecutionError):
    code = "authentication_failed"


class ConnectionFailedError(ProphylaxisExecutionError):
    code = "connection_failed"


class SSHNegotiationFailedError(ProphylaxisExecutionError):
    code = "ssh_negotiation_failed"


class ParseFailedError(ProphylaxisExecutionError):
    code = "parse_failed"


__all__ = [
    "AmbiguousServicePortError",
    "AnsibleContentError",
    "AnsibleExecutionError",
    "AuthenticationFailedError",
    "DeviceAmbiguousError",
    "DeviceInactiveError",
    "DeviceNotFoundError",
    "DeviceResolutionError",
    "CheckNotSelectedError",
    "ConnectionFailedError",
    "ConnectionTimeoutError",
    "HostKeyMismatchError",
    "HostKeyUnknownError",
    "InvalidServicePortError",
    "MissingCredentialProfileError",
    "MissingDeviceNameError",
    "MissingManufacturerError",
    "MissingPlatformError",
    "MissingPrimaryIPError",
    "ParseFailedError",
    "ProphylaxisExecutionError",
    "ProphylaxisDisabledError",
    "SSHNegotiationFailedError",
    "UnsupportedPlatformError",
]
