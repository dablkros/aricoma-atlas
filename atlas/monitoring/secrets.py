"""Provisioner identity, CAS-first device-specific secrets, and no rotation."""
import secrets
import string
from atlas.device_credentials import DeviceValidationError, credential_path
from atlas.monitoring.models import MonitoringError
from atlas.services.errors import DependencyError


class MonitoringSecrets:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def path(device):
        try:
            return credential_path(device.vendor, device.name, purpose="snmp")
        except DeviceValidationError:
            raise MonitoringError("invalid_device_credential_name") from None

    @staticmethod
    def legacy_path(device):
        return f"devices/snmp/{device.vendor}/{device.id}/monitoring"

    def _read_path(self, device, path, *, require_name):
        try:
            data, metadata = self.service.read_secret_with_metadata(path)
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                return None
            raise
        invalid = (data.get("version") != "2c" or data.get("managed_by") != "atlas"
                or data.get("device_id") != str(device.id)
                or (require_name and data.get("device_name") != device.name)
                or not isinstance(data.get("community"), str)
                or not 1 <= len(data["community"]) <= 35
                or any(c not in string.ascii_letters + string.digits for c in data["community"]))
        if device.platform == "fortios":
            invalid = invalid or (
                type(data.get("community_id")) is not int
                or not 1 <= data["community_id"] <= 4294967295
                or not isinstance(data.get("vdom"), str)
            )
        if device.platform in {"cisco-cbs", "cisco-cbs250-350"}:
            invalid = invalid or len(data.get("community", "")) > 20
        if invalid:
            raise MonitoringError("invalid_monitoring_secret")
        return data, metadata["version"]

    def read(self, device):
        return self._read_path(
            device,
            self.path(device),
            require_name=True,
        ) or self._read_path(
            device,
            self.legacy_path(device),
            require_name=False,
        )

    def _write_cas(self, path, data):
        self.service._authenticated(lambda token: self.service.client.kv_write(
            token, path, data, mount="atlas", cas=0))

    def ensure(self, device, community_id=None, options=None):
        path = self.path(device)
        existing = self._read_path(device, path, require_name=True)
        if existing:
            return existing
        legacy = self._read_path(
            device,
            self.legacy_path(device),
            require_name=False,
        )
        if legacy:
            migrated = {**legacy[0], "device_name": device.name}
            try:
                self._write_cas(path, migrated)
            except DependencyError:
                winner = self._read_path(device, path, require_name=True)
                if winner:
                    return winner
                raise
            return self._read_path(device, path, require_name=True)
        length = 20 if device.platform in {"cisco-cbs", "cisco-cbs250-350"} else 32
        data = {
            "version": "2c",
            "community": "".join(
                secrets.choice(string.ascii_letters + string.digits)
                for _ in range(length)
            ),
            "managed_by": "atlas",
            "device_id": str(device.id),
            "device_name": device.name,
        }
        if device.platform == "fortios":
            if (
                type(community_id) is not int
                or not isinstance(options, dict)
                or not isinstance(options.get("vdom"), str)
            ):
                raise MonitoringError("invalid_monitoring_secret")
            data.update({"community_id": community_id, "vdom": options["vdom"]})
        try:
            self._write_cas(path, data)
        except DependencyError:
            # CAS races may only reuse the winner at this exact path; no fallback.
            winner = self._read_path(device, path, require_name=True)
            if winner:
                return winner
            raise
        return self._read_path(device, path, require_name=True)
