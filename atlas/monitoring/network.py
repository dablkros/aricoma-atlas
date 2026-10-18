"""Vendor-neutral monitoring driver backed by explicit Ansible platform tasks."""

from atlas.monitoring.models import MonitoringError


class AnsibleNetworkMonitoringDriver:
    def __init__(
        self,
        device,
        options,
        credentials,
        profile,
        backend,
        secret=None,
    ):
        self.device = device
        self.options = options
        self.credentials = credentials
        self.profile = profile
        self.backend = backend
        self.secret = secret

    def resolve_vdom(self):
        return None

    def check_schema(self):
        return None

    def _execute(self, operation, secret):
        return self.backend.execute(
            host=self.device.address,
            port=self.options["ssh_port"],
            profile=self.profile,
            credentials=self.credentials,
            operation=operation,
            options=self.options,
            secret=secret,
        )

    def inspect(self):
        # The placeholder is process-local and never persisted; it permits a
        # read-only connectivity/configuration inspection before CAS creates
        # the real per-device secret.
        secret = self.secret or {"community": "A" * 64}
        return self._execute("inspect", secret)

    @staticmethod
    def plan(state, _secret):
        return [] if state["compliant"] else ["Configure Atlas SNMPv2c policy"]

    @staticmethod
    def free_id(_state):
        return None

    def configure(self, secret, changed):
        if (
            self.device.platform == "aruba-aos-switch"
            and not self.options.get("allow_unrestricted_snmp_v2c")
        ):
            raise MonitoringError("unrestricted_snmp_v2c_not_allowed")
        self.secret = secret
        if self.inspect()["compliant"]:
            return
        # Persist intent before Runner starts. A timeout may mean that the
        # device accepted the command even though Atlas did not receive a result.
        changed("Configure Atlas SNMPv2c policy")
        result = self._execute("configure", secret)
        if not result["compliant"] or not self.inspect()["compliant"]:
            raise MonitoringError("device_configuration_verification_failed", 503)


class FortiAnsibleConfigurator:
    def __init__(self, device, options, credentials, profile, backend):
        self.device = device
        self.options = options
        self.credentials = credentials
        self.profile = profile
        self.backend = backend

    def configure(self, secret):
        result = self.backend.execute(
            host=self.device.address,
            port=self.options["https_port"],
            profile=self.profile,
            credentials=self.credentials,
            operation="configure",
            options=self.options,
            secret=secret,
        )
        if not result["compliant"]:
            raise MonitoringError("fortigate_configuration_verification_failed", 503)


__all__ = ["AnsibleNetworkMonitoringDriver", "FortiAnsibleConfigurator"]
