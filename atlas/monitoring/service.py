"""Desired-state workflow; safe retry instead of cross-system rollback."""
from dataclasses import asdict
import hashlib
import json
import time

from atlas.device_credentials import DeviceValidationError
from atlas.integrations.zabbix import ZabbixAPIError
from atlas.monitoring.fortigate import DRIVERS
from atlas.monitoring.models import MonitoringError, device_reference, device_options, load_config
from atlas.monitoring.secrets import MonitoringSecrets
from atlas.monitoring.store import JobStore, TERMINAL
from atlas.monitoring.zabbix import MonitoringZabbix
from atlas.services.errors import DependencyError
from atlas.zabbix_config import load_zabbix_config

MODES = {"monitor_only", "provision_and_monitor"}


class MonitoringService:
    def __init__(self, netbox, openbao, zabbix, config_file, directory, driver_factory=None):
        self.netbox, self.openbao, self.zabbix = netbox, openbao, zabbix
        self.config_file = config_file
        self.store = JobStore(directory)
        self.secrets = MonitoringSecrets(openbao)
        self.driver_factory = driver_factory

    def list_devices(self):
        result = []
        for raw in self.netbox.get_monitoring_devices():
            if (raw.get("custom_fields") or {}).get("monitoring_enabled") is not True:
                continue
            try:
                device = device_reference(raw)
                result.append({**asdict(device), "monitoring_enabled": True, "provisioning_status": "NOT_INSPECTED", "zabbix_status": "NOT_INSPECTED"})
            except MonitoringError as exc:
                result.append({"id": raw.get("id"), "name": raw.get("name"), "monitoring_enabled": True,
                               "error_code": exc.code, "provisioning_status": "UNSUPPORTED"})
        return result

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
        device = device_reference(self.netbox.get(f"/api/dcim/devices/{device_id}/"))
        if device.id != device_id:
            raise MonitoringError("netbox_identity_mismatch", 502)
        options = device_options(device, config)
        self.openbao.check_ready()  # Sealed or unauthenticated => no downstream write.
        stored = self.secrets.read(device, config["netbox_instance"])
        if stored is None and mode == "monitor_only":
            raise MonitoringError("snmp_credentials_missing")
        secret, version = stored if stored else (None, 0)
        driver, state = None, None
        device_changes = []
        if mode == "provision_and_monitor":
            credentials = self.openbao.get_device_credentials(device.vendor, device.profile, authentication="api_token")
            factory = self.driver_factory or DRIVERS[(device.vendor, device.platform)]
            driver = factory(device, options, credentials["api_token"])
            driver.check_schema()
            state = driver.inspect()
            device_changes = driver.plan(state, secret)
        template = load_zabbix_config()["platform_templates"].get(device.platform)
        if not template:
            raise MonitoringError("template_mapping_missing")
        zabbix = MonitoringZabbix(self.zabbix.client(), device, options, config["netbox_instance"], template)
        remote = zabbix.inspect()  # Conflicts/template errors precede secret/device writes.
        actions = (["Store device SNMP credentials with CAS"] if secret is None else []) + device_changes + zabbix.actions(remote, version or 1)
        public = {"device": asdict(device), "mode": mode, "planned_changes": actions,
                  "monitoring_sources": options["source_ips"], "verification": "PENDING",
                  "device_configuration": "NOT_CHANGED" if mode == "monitor_only" else "INSPECTED",
                  "zabbix_status": "EXISTS" if remote["host"] else "MISSING"}
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
                    raise MonitoringError("state_changed_replan_required", 409)
                device, options, config, secret, version, driver, state, zabbix = context
                def changed(label):
                    # Persist write intent before the request; a timeout may mean it applied.
                    self.store.update(job, mutation_attempted=True, changes=[*job["changes"], label])
                if job["mode"] == "provision_and_monitor":
                    if secret is None:
                        changed("Store device SNMP credentials with CAS")
                        secret, version = self.secrets.ensure(device, config["netbox_instance"], driver.free_id(state), options)
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
