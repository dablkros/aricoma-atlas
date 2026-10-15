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
        return f"devices/snmp/fortinet/{device.id}/monitoring"

    def read(self, device):
        try:
            data, metadata = self.service.read_secret_with_metadata(self.path(device))
        except DependencyError as exc:
            if exc.reason == "secret_not_found":
                return None
            raise
        if (data.get("version") != "2c" or data.get("managed_by") != "atlas"
                or data.get("device_id") != str(device.id)
                or not isinstance(data.get("community"), str)
                or not 1 <= len(data["community"]) <= 35
                or any(c not in string.ascii_letters + string.digits for c in data["community"])
                or type(data.get("community_id")) is not int or not 1 <= data["community_id"] <= 4294967295):
            raise MonitoringError("invalid_monitoring_secret")
        return data, metadata["version"]

    def ensure(self, device, community_id, options):
        existing = self.read(device)
        if existing:
            return existing
        data = {"version": "2c", "community": "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32)),
                "managed_by": "atlas", "device_id": str(device.id),
                "community_id": community_id, "vdom": options["vdom"]}
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
