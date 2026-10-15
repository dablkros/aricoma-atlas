"""FortiOS CMDB adapter. No raw responses or credentials cross this boundary."""
from urllib.parse import quote
import requests
from atlas.monitoring.models import MonitoringError


class FortiGateMonitoringDriver:
    def __init__(self, device, options, token, requester=None):
        self.device, self.options = device, options
        self.token = token
        self.requester = requester or requests
        self.base = f"https://{device.address}:{options['https_port']}/api/v2/cmdb/"

    def request(self, method, resource, payload=None, schema=False, vdom="resolved"):
        params = {}
        if vdom == "resolved":
            vdom = self.options.get("vdom")
        if vdom is not None:
            params["vdom"] = vdom
        if schema:
            params["action"] = "schema"
        try:
            response = self.requester.request(method, self.base + resource,
                headers={"Authorization": f"Bearer {self.token}"}, params=params,
                json=payload, timeout=(5, 20), allow_redirects=False,
                verify=self.options.get("ca_bundle") or self.options["tls_verify"])
            if response.status_code in (401, 403):
                raise MonitoringError("fortigate_permission_denied", 503)
            if response.status_code == 404:
                raise MonitoringError("fortigate_object_not_found", 404)
            if not 200 <= response.status_code < 300:
                raise MonitoringError("fortigate_api_failed", 503)
            body = response.json()
        except (requests.RequestException, ValueError):
            raise MonitoringError("fortigate_unavailable", 503) from None
        if not isinstance(body, dict) or body.get("status") != "success":
            raise MonitoringError("fortigate_api_failed", 503)
        if str(body.get("version", "")).lstrip("v") != "7.0.9":
            raise MonitoringError("unsupported_fortios_version")
        return body.get("results")

    @staticmethod
    def one(value):
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not isinstance(value, dict):
            raise MonitoringError("fortigate_invalid_response", 502)
        return value

    def inspect(self):
        info = self.one(self.request("GET", "system.snmp/sysinfo"))
        communities = self.request("GET", "system.snmp/community")
        interface = self.one(self.request("GET", "system/interface/" + quote(self.options["interface"], safe="")))
        if not isinstance(communities, list) or any(not isinstance(c, dict) for c in communities):
            raise MonitoringError("fortigate_invalid_response", 502)
        allow = interface.get("allowaccess")
        if not isinstance(allow, str):
            raise MonitoringError("fortigate_allowaccess_schema_unknown")
        return {"sysinfo": info, "communities": communities, "interface": interface}

    def resolve_vdom(self):
        global_state = self.one(self.request("GET", "system/global", vdom=None))
        mode = global_state.get("vdom-mode")
        default = self.options["default_vdom"]
        resource = "system/interface/" + quote(self.options["interface"], safe="")
        if mode in {"no-vdom", "disable"}:
            # A non-VDOM FortiGate may reject an otherwise valid request when
            # an artificial ?vdom=root query parameter is supplied.
            self.one(self.request("GET", resource, vdom=None))
            self.options["vdom"] = default
            return default
        if mode not in {"multi-vdom", "split-vdom", "enable"}:
            raise MonitoringError("vdom_ambiguous")
        try:
            interface = self.one(self.request("GET", resource, vdom=default))
        except MonitoringError as exc:
            if exc.code != "fortigate_object_not_found":
                raise
        else:
            value = interface.get("vdom")
            if isinstance(value, dict):
                value = value.get("name")
            if value in {None, default}:
                self.options["vdom"] = default
                return default
            raise MonitoringError("vdom_ambiguous")
        interfaces = self.request("GET", resource, vdom="*")
        if isinstance(interfaces, dict):
            interfaces = [interfaces]
        if not isinstance(interfaces, list):
            raise MonitoringError("vdom_ambiguous")
        vdoms = set()
        for interface in interfaces:
            if not isinstance(interface, dict) or interface.get("name") not in {None, self.options["interface"]}:
                continue
            value = interface.get("vdom")
            if isinstance(value, dict):
                value = value.get("name")
            if isinstance(value, str) and value:
                vdoms.add(value)
        if len(vdoms) != 1:
            raise MonitoringError("vdom_ambiguous")
        self.options["vdom"] = vdoms.pop()
        return self.options["vdom"]

    def check_schema(self):
        # Fail closed if firmware exposes a different CMDB schema.
        required = {
            "system.snmp/sysinfo": {"status"},
            "system.snmp/community": {"id", "name", "hosts", "hosts6", "status", "query-v1-status", "query-v2c-status", "query-v2c-port", "trap-v1-status", "trap-v2c-status"},
            "system/interface": {"allowaccess"},
        }
        def keys(value):
            if isinstance(value, dict):
                result = set(value)
                if isinstance(value.get("name"), str):
                    result.add(value["name"])
                for nested in value.values():
                    result.update(keys(nested))
                return result
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value)) if value else set()
            return set()
        for resource, fields in required.items():
            schema = self.request("GET", resource, schema=True)
            if not fields <= keys(schema):
                raise MonitoringError("fortigate_schema_not_supported")

    def desired_community(self, secret):
        if secret.get("vdom") != self.options["vdom"]:
            raise MonitoringError("snmp_community_conflict", 409)
        return {"id": secret["community_id"], "name": secret["community"], "status": "enable",
                "query-v1-status": "disable", "query-v2c-status": "enable", "query-v2c-port": self.options["snmp_port"],
                "trap-v1-status": "disable", "trap-v2c-status": "disable", "hosts6": [],
                "hosts": [{"id": i + 1, "ip": f"{ip} 255.255.255.255", "host-type": "query"}
                          for i, ip in enumerate(self.options["source_ips"])]}

    @staticmethod
    def community_equal(actual, desired):
        for key, value in desired.items():
            if key == "hosts":
                current = sorted((h.get("ip"), h.get("host-type")) for h in actual.get("hosts", []))
                expected = sorted((h["ip"], h["host-type"]) for h in value)
                if current != expected:
                    return False
            elif actual.get(key) != value:
                return False
        return True

    def plan(self, state, secret):
        changes = []
        if state["sysinfo"].get("status") != "enable":
            changes.append("Enable SNMP agent")
        if "snmp" not in state["interface"]["allowaccess"].split():
            changes.append("Add SNMP to management interface access")
        if secret is None:
            changes.append("Create Atlas community restricted to monitoring sources")
            return changes
        desired = self.desired_community(secret)
        owned = [c for c in state["communities"] if c.get("id") == secret["community_id"]]
        if len(owned) > 1 or (owned and owned[0].get("name") != secret["community"]):
            raise MonitoringError("snmp_community_conflict", 409)
        if any(c.get("name") == secret["community"] and c.get("id") != secret["community_id"] for c in state["communities"]):
            raise MonitoringError("snmp_community_conflict", 409)
        if not owned:
            changes.append("Create Atlas community restricted to monitoring sources")
        elif not self.community_equal(owned[0], desired):
            changes.append("Reconcile Atlas community restrictions")
        return changes

    @staticmethod
    def free_id(state):
        taken = {c.get("id") for c in state["communities"]}
        return next(i for i in range(1, 1000000) if i not in taken)

    def configure(self, secret, changed):
        self.check_schema()
        # Re-read immediately before each write; never replace whole tables.
        state = self.inspect()
        self.plan(state, secret)
        desired = self.desired_community(secret)
        existing = next((c for c in state["communities"] if c.get("id") == secret["community_id"]), None)
        if not existing or not self.community_equal(existing, desired):
            changed("Configure Atlas SNMP community")
            resource = "system.snmp/community" + (f"/{secret['community_id']}" if existing else "")
            self.request("PUT" if existing else "POST", resource, desired)
        info = self.one(self.request("GET", "system.snmp/sysinfo"))
        if info.get("status") != "enable":
            changed("Enable SNMP agent")
            self.request("PUT", "system.snmp/sysinfo", {"status": "enable"})
        resource = "system/interface/" + quote(self.options["interface"], safe="")
        interface = self.one(self.request("GET", resource))
        access = interface.get("allowaccess")
        if not isinstance(access, str):
            raise MonitoringError("fortigate_allowaccess_schema_unknown")
        if "snmp" not in access.split():
            changed("Add SNMP to management interface access")
            self.request("PUT", resource, {"allowaccess": " ".join([*access.split(), "snmp"])})
        if self.plan(self.inspect(), secret):
            raise MonitoringError("fortigate_configuration_verification_failed", 503)


DRIVERS = {("fortinet", "fortios"): FortiGateMonitoringDriver}
