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


__all__ = [
    "DeviceAmbiguousError",
    "DeviceNotFoundError",
    "DeviceResolutionError",
    "MissingCredentialProfileError",
    "MissingDeviceNameError",
    "MissingManufacturerError",
    "MissingPlatformError",
    "MissingPrimaryIPError",
]
