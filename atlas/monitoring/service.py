"""Desired-state workflow; safe retry instead of cross-system rollback."""
from dataclasses import asdict
import hashlib
import json
import time

from atlas.device_credentials import DeviceValidationError, credential_path
from atlas.integrations.zabbix import ZabbixAPIError
from atlas.monitoring.fortigate import DRIVERS
from atlas.monitoring.models import (
    MonitoringError,
    device_reference,
    ensure_device_network_allowed,
    load_config,
    site_options,
)
from atlas.monitoring.secrets import MonitoringSecrets
from atlas.monitoring.store import JobStore
from atlas.monitoring.zabbix import MonitoringZabbix
from atlas.services.errors import DependencyError
from atlas.services.device_resolution import (
    DeviceResolutionError,
    resolve_primary_interface,
    resolve_tcp_service_port,
)
from atlas.zabbix_config import load_zabbix_config

MODES = {"monitor_only", "provision_and_monitor"}


class MonitoringService:
    def __init__(
        self,
        netbox,
        openbao,
        zabbix,
        config_file,
        directory,
        driver_factory=None,
        credential_source=None,
    ):
        self.netbox, self.openbao, self.zabbix = netbox, openbao, zabbix
        # Read operator-managed admin credentials through the same resolver
        # used by Profylaxia. Keep the monitoring AppRole scoped to generated
        # per-device SNMP secrets.
        self.credential_source = credential_source or openbao
        self.config_file = config_file
        self.store = JobStore(directory)
        self.secrets = MonitoringSecrets(openbao)
        self.driver_factory = driver_factory

    def list_devices(self):
        config = load_config(self.config_file)
        result = []
        for raw in self.netbox.get_monitoring_devices():
            if (raw.get("custom_fields") or {}).get("monitoring_enabled") is not True:
                continue
            try:
                device = device_reference(raw)
                options = self._resolve_options(device, raw, config)
                result.append({**asdict(device), "monitoring_enabled": True,
                    "provisioning_status": "READY_TO_INSPECT", "zabbix_status": "NOT_INSPECTED",
                    "resolved": {"api_endpoint": f"https://{device.address}:{options['https_port']}",
                        "management_interface": options["interface"],
                        "snmp_port": options["snmp_port"],
                        "monitoring_source_ip": options["source_ips"][0]}})
            except MonitoringError as exc:
                result.append({"id": raw.get("id"), "name": raw.get("name"), "monitoring_enabled": True,
                               "error_code": exc.code, "provisioning_status": "BLOCKED"})
        return result

    def _resolve_options(self, device, raw, config):
        # The allowlist is checked before any device-oriented NetBox lookup.
        ensure_device_network_allowed(device, config)
        try:
            services = self.netbox.get_device_services(device.id, "fortios-api")
            https_port = resolve_tcp_service_port(
                services, config["fortios"]["default_https_port"]
            )
        except DeviceResolutionError:
            raise MonitoringError("fortios_api_service_invalid") from None
        try:
            interface = resolve_primary_interface(self.netbox, raw)
        except DeviceResolutionError:
            raise MonitoringError("management_interface_unresolved") from None
        return site_options(device, config, https_port=https_port, interface=interface)

    @staticmethod
    def fingerprint(value):
        # Ignore operational timestamps/availability, not configuration or identity.
        def stable(v):
            if isinstance(v, dict):
                return {k: stable(x) for k, x in v.items() if k not in {"lastclock", "lastvalue", "available", "error", "state", "errors_from", "disable_until"}}
            if isinstance(v, list):
                return [stable(x) for x in v]
            return v
        return hashlib.sha256(json.dumps(stable(value), sort_keys=True, default=str).encode()).hexdigest()

    def inspect(self, device_id, mode):
        if mode not in MODES:
            raise MonitoringError("invalid_mode")
        config = load_config(self.config_file)
        raw = self.netbox.get(f"/api/dcim/devices/{device_id}/")
        device = device_reference(raw)
        if device.id != device_id:
            raise MonitoringError("netbox_identity_mismatch", 502)
        options = self._resolve_options(device, raw, config)
        self.openbao.check_ready()  # Sealed or unauthenticated => no downstream write.
        stored = self.secrets.read(device)
        if stored is None and mode == "monitor_only":
            raise MonitoringError("snmp_credentials_missing")
        secret, version = stored if stored else (None, 0)
        credentials = self.credential_source.get_device_credentials(
            device.vendor, device.profile, authentication="api_token"
        )
        factory = self.driver_factory or DRIVERS[(device.vendor, device.platform)]
        driver = factory(device, options, credentials["api_token"])
        driver.resolve_vdom()
        if secret is not None and secret.get("vdom") != options["vdom"]:
            raise MonitoringError("snmp_community_conflict", 409)
        driver.check_schema()
        state = driver.inspect()
        device_changes = driver.plan(state, secret) if mode == "provision_and_monitor" else []
        template = load_zabbix_config()["platform_templates"].get(device.platform)
        if not template:
            raise MonitoringError("template_mapping_missing")
        zabbix = MonitoringZabbix(self.zabbix.client(), device, options, template)
        remote = zabbix.inspect()  # Conflicts/template errors precede secret/device writes.
        actions = (["Store device SNMP credentials with CAS"] if secret is None else []) + device_changes + zabbix.actions(remote, version or 1)
        public = {"device": asdict(device), "mode": mode, "planned_changes": actions,
                  "monitoring_sources": options["source_ips"], "verification": "PENDING",
                  "device_configuration": "NOT_CHANGED" if mode == "monitor_only" else "INSPECTED",
                  "zabbix_status": "EXISTS" if remote["host"] else "MISSING",
                  "resolved": {"api_endpoint": f"https://{device.address}:{options['https_port']}",
                    "management_interface": options["interface"], "vdom": options["vdom"],
                    "snmp_port": options["snmp_port"], "monitoring_source_ip": options["source_ips"][0],
                    "zabbix_template": template, "credential_profile": device.profile,
                    "credential_reference": credential_path(device.vendor, device.profile)}}
        fingerprint = self.fingerprint({"device": asdict(device), "config": config, "options": options,
            "secret": secret, "version": version, "fortigate": state, "zabbix": remote, "mode": mode})
        return public, fingerprint, (device, options, config, secret, version, driver, state, zabbix)

    def plan(self, device_id, mode):
        public, fingerprint, _ = self.inspect(device_id, mode)
        plan_id = self.store.plan({"device_id": device_id, "mode": mode, "fingerprint": fingerprint})
        return {**public, "plan_id": plan_id, "expires_in": 900}

    def submit(self, device_id, mode, plan_id):
        plan = self.store.get_plan(plan_id)
        if plan["device_id"] != device_id or plan["mode"] != mode:
            raise MonitoringError("plan_identity_mismatch", 409)
        return self.store.create(device_id, mode)

    def run(self, job_id, plan_id):
        job = self.store.get(job_id)
        try:
            with self.store.lock(f"device-{job['device_id']}"), self.store.lock("zabbix"):
                self.store.update(job, status="VALIDATING", current_step="VALIDATING")
                approved = self.store.get_plan(plan_id)
                public, fingerprint, context = self.inspect(job["device_id"], job["mode"])
                if fingerprint != approved["fingerprint"]:
                    raise MonitoringError("plan_stale", 409)
                device, options, config, secret, version, driver, state, zabbix = context
                def changed(label):
                    # Persist write intent before the request; a timeout may mean it applied.
                    self.store.update(job, mutation_attempted=True, changes=[*job["changes"], label])
                if job["mode"] == "provision_and_monitor":
                    if secret is None:
                        changed("Store device SNMP credentials with CAS")
                        secret, version = self.secrets.ensure(device, driver.free_id(state), options)
                    self.store.update(job, status="CONFIGURING_DEVICE", current_step="CONFIGURING_DEVICE")
                    driver.configure(secret, changed)
                    self.store.update(job, device_configuration="CONFIGURED")
                else:
                    self.store.update(job, device_configuration="NOT_CHANGED")
                self.store.update(job, status="CONFIGURING_ZABBIX", current_step="CONFIGURING_ZABBIX")
                zabbix.configure(secret, version, changed)
                self.store.update(job, zabbix_registration="CONFIGURED", status="VERIFYING", current_step="VERIFYING")
                since = job["started_at"] if job["changes"] else time.time() - 300
                verification = zabbix.verify(since)
                self.store.update(job, status="COMPLETED", current_step="COMPLETED", finished_at=time.time(), verification=verification)
        except Exception as exc:
            code = (exc.code if isinstance(exc, (MonitoringError, DeviceValidationError)) else
                    f"dependency_{exc.reason}" if isinstance(exc, DependencyError) else
                    f"zabbix_{exc.code}" if isinstance(exc, ZabbixAPIError) else "monitoring_internal_error")
            # No exception messages, upstream payloads or tracebacks in durable results.
            self.store.update(job, status="PARTIAL" if job["mutation_attempted"] else "FAILED", current_step="STOPPED",
                finished_at=time.time(), error_code=code, error_message="Operation stopped; inspect current state and plan a safe retry")

    def job(self, identifier):
        job = self.store.get(identifier)
        if job["status"] == "COMPLETED" and job["verification"] == "PENDING":
            try:
                _, _, context = self.inspect(job["device_id"], "monitor_only")
                verification = context[-1].verify(max(job["started_at"], time.time() - 300))
                self.store.update(job, verification=verification)
            except Exception:
                pass  # Retain PENDING, never infer successful monitoring.
        return {k: v for k, v in job.items() if k != "mutation_attempted"}
