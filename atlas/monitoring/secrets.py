"""Provisioner identity, CAS-first device-specific secrets, and no rotation."""
import secrets
import string
from atlas.monitoring.models import MonitoringError
from atlas.services.errors import DependencyError


class MonitoringSecrets:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def path(device):
        return f"devices/snmp/{device.vendor}/{device.id}/monitoring"

    def read(self, device):
        try:
            data, metadata = self.service.read_secret_with_metadata(self.path(device))
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                return None
            raise
        invalid = (data.get("version") != "2c" or data.get("managed_by") != "atlas"
                or data.get("device_id") != str(device.id)
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

    def ensure(self, device, community_id=None, options=None):
        existing = self.read(device)
        if existing:
            return existing
        length = 20 if device.platform in {"cisco-cbs", "cisco-cbs250-350"} else 32
        data = {
            "version": "2c",
            "community": "".join(
                secrets.choice(string.ascii_letters + string.digits)
                for _ in range(length)
            ),
            "managed_by": "atlas",
            "device_id": str(device.id),
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
            self.service._authenticated(lambda token: self.service.client.kv_write(
                token, self.path(device), data, mount="atlas", cas=0))
        except DependencyError:
            # CAS races may only reuse the winner at this exact path; no fallback.
            winner = self.read(device)
            if winner:
                return winner
            raise
        return self.read(device)
