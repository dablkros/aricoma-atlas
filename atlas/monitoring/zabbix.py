"""P0 reconciliation through the existing Zabbix JSON-RPC client (7.0)."""
import time
from atlas.monitoring.models import MonitoringError
from atlas.services.zabbix import MANAGED_BY_TAG, MANAGED_BY_VALUE, NETBOX_ID_TAG

MACRO = "{$SNMP_COMMUNITY}"
OID = "1.3.6.1.2.1.1.2.0"
KEY = "atlas.snmp.sysObjectID"


def tags(host):
    return {v["tag"]: v["value"] for v in host.get("tags", [])}


class MonitoringZabbix:
    def __init__(self, client, device, options, instance, template):
        self.client, self.device, self.options = client, device, options
        self.instance, self.template = instance, template
        self.group = f"Atlas/{instance}/{device.site}"

    def inspect(self):
        if not self.client.api_version().startswith("7.0."):
            raise MonitoringError("unsupported_zabbix_version")
        hosts = self.client.host_get({"output": ["hostid", "host", "name", "status", "monitored_by", "proxyid"],
            "selectTags": "extend", "selectHostGroups": ["groupid", "name"],
            "selectParentTemplates": ["templateid"], "selectInterfaces": "extend"})
        owned = []
        for host in hosts:
            values = tags(host)
            is_owned = (values.get("atlas_managed") == "true"
                and values.get("atlas_netbox_device_id") == str(self.device.id)
                and values.get("atlas_netbox_instance") == self.instance
                and values.get("atlas_provisioning") == "p0")
            if is_owned:
                owned.append(host)
            elif (host.get("host") == self.device.name or any(i.get("ip") == self.device.address for i in host.get("interfaces", []))
                  or (values.get(NETBOX_ID_TAG) == str(self.device.id) and values.get(MANAGED_BY_TAG) == MANAGED_BY_VALUE
                      and values.get("atlas_netbox_instance", self.instance) == self.instance)):
                raise MonitoringError("zabbix_host_ownership_conflict", 409)
        if len(owned) > 1:
            raise MonitoringError("zabbix_duplicate_identity", 409)
        host = owned[0] if owned else None
        if host and len([i for i in host.get("interfaces", []) if str(i.get("type")) == "2"]) > 1:
            raise MonitoringError("zabbix_ambiguous_snmp_interface", 409)
        templates = self.client.template_get({"output": ["templateid", "host"], "filter": {"host": [self.template]}})
        if len(templates) != 1:
            raise MonitoringError("zabbix_template_missing")
        groups = self.client.hostgroup_get({"output": ["groupid", "name"], "filter": {"name": [self.group]}})
        if len(groups) > 1:
            raise MonitoringError("zabbix_group_conflict", 409)
        proxy_id = None
        if self.options.get("proxy_name"):
            proxies = self.client.call("proxy.get", {"output": ["proxyid", "name"], "filter": {"name": [self.options["proxy_name"]]}})
            if len(proxies) != 1:
                raise MonitoringError("zabbix_proxy_missing")
            proxy_id = proxies[0]["proxyid"]
        macros = self.client.call("usermacro.get", {"output": ["hostmacroid", "macro", "type", "description"],
            "hostids": [host["hostid"]], "filter": {"macro": [MACRO]}}) if host else []
        items = self.client.item_get({"output": ["itemid", "key_", "snmp_oid", "type", "interfaceid", "status", "state", "lastclock", "lastvalue"],
            "hostids": [host["hostid"]], "filter": {"key_": [KEY]}}) if host else []
        if len(macros) > 1 or len(items) > 1:
            raise MonitoringError("zabbix_duplicate_objects", 409)
        return {"host": host, "template": templates[0], "group": groups[0] if groups else None,
                "proxy_id": proxy_id, "macro": macros[0] if macros else None, "item": items[0] if items else None}

    def wanted_tags(self, secret_version):
        return {MANAGED_BY_TAG: MANAGED_BY_VALUE, NETBOX_ID_TAG: str(self.device.id),
                "atlas_managed": "true", "atlas_netbox_device_id": str(self.device.id),
                "atlas_netbox_instance": self.instance, "atlas_provisioning": "p0",
                "atlas_snmp_secret_version": str(secret_version)}

    def interface(self):
        return {"type": 2, "main": 1, "useip": 1, "ip": self.device.address, "dns": "",
                "port": str(self.options["snmp_port"]),
                "details": {"version": 2, "bulk": 1, "community": MACRO}}

    def macro_description(self, version):
        return f"Atlas monitoring {self.instance}/{self.device.id} KV version {version}"

    def actions(self, state, version):
        host = state["host"]
        if not host:
            return ["Create Zabbix host with SNMP interface, secret macro, template and group", "Create sysObjectID verification item"]
        actions = []
        if host.get("host") != self.device.name or host.get("name") != self.device.name or str(host.get("status")) != "0":
            actions.append("Update Zabbix host identity/status")
        if any(tags(host).get(k) != v for k, v in self.wanted_tags(version).items()):
            actions.append("Update Atlas ownership tags")
        if not state["group"] or str(state["group"]["groupid"]) not in {str(g["groupid"]) for g in host.get("hostgroups", [])}:
            actions.append("Link Atlas site group")
        if str(state["template"]["templateid"]) not in {str(t["templateid"]) for t in host.get("parentTemplates", [])}:
            actions.append("Link monitoring template")
        if str(host.get("monitored_by", "0")) != ("1" if state["proxy_id"] else "0") or (state["proxy_id"] and str(host.get("proxyid")) != str(state["proxy_id"])):
            actions.append("Configure monitoring source")
        snmp = next((i for i in host.get("interfaces", []) if str(i.get("type")) == "2"), None)
        wanted = self.interface()
        if not snmp or any(str(snmp.get(k)) != str(v) for k, v in wanted.items() if k != "details") or any(
                str((snmp.get("details") or {}).get(k)) != str(v) for k, v in wanted["details"].items()):
            actions.append("Configure SNMP interface")
        macro = state["macro"]
        if not macro or str(macro.get("type")) != "1" or macro.get("description") != self.macro_description(version):
            actions.append("Synchronize secret community macro")
        item = state["item"]
        if item and (str(item.get("type")) != "20" or item.get("snmp_oid") != OID):
            raise MonitoringError("zabbix_verification_item_conflict", 409)
        if not item:
            actions.append("Create sysObjectID verification item")
        elif str(item.get("status")) != "0" or not snmp or str(item.get("interfaceid")) != str(snmp.get("interfaceid")):
            actions.append("Reconcile sysObjectID verification item")
        return actions

    def configure(self, secret, version, changed):
        state = self.inspect()
        actions = self.actions(state, version)
        if not state["group"]:
            changed("Create Atlas site group")
            groupid = self.client.hostgroup_create(self.group)
        else:
            groupid = state["group"]["groupid"]
        host = state["host"]
        macro = {"macro": MACRO, "value": secret["community"], "type": 1, "description": self.macro_description(version)}
        wanted_tags = self.wanted_tags(version)
        if not host:
            changed("Create Zabbix host")
            hostid = self.client.host_create({"host": self.device.name, "name": self.device.name, "status": 0,
                "groups": [{"groupid": groupid}], "templates": [{"templateid": state["template"]["templateid"]}],
                "interfaces": [self.interface()], "macros": [macro],
                "tags": [{"tag": k, "value": v} for k, v in wanted_tags.items()],
                "monitored_by": 1 if state["proxy_id"] else 0,
                **({"proxyid": state["proxy_id"]} if state["proxy_id"] else {})})
        else:
            hostid = host["hostid"]
            host_actions = set(actions) & {"Update Zabbix host identity/status", "Update Atlas ownership tags", "Link Atlas site group", "Link monitoring template", "Configure monitoring source"}
            if host_actions:
                changed("Reconcile Atlas Zabbix host links")
                merged_tags = {**tags(host), **wanted_tags}
                groupids = {str(g["groupid"]) for g in host.get("hostgroups", [])} | {str(groupid)}
                templateids = {str(t["templateid"]) for t in host.get("parentTemplates", [])} | {str(state["template"]["templateid"])}
                self.client.host_update({"hostid": hostid, "host": self.device.name, "name": self.device.name, "status": 0,
                    "tags": [{"tag": k, "value": v} for k, v in merged_tags.items()],
                    "groups": [{"groupid": v} for v in sorted(groupids)], "templates": [{"templateid": v} for v in sorted(templateids)],
                    "monitored_by": 1 if state["proxy_id"] else 0,
                    **({"proxyid": state["proxy_id"]} if state["proxy_id"] else {})})
            if "Configure SNMP interface" in actions:
                changed("Configure SNMP interface")
                snmp = next((i for i in host.get("interfaces", []) if str(i.get("type")) == "2"), None)
                self.client.call("hostinterface.update" if snmp else "hostinterface.create",
                    {**self.interface(), **({"interfaceid": snmp["interfaceid"]} if snmp else {"hostid": hostid})})
            if "Synchronize secret community macro" in actions:
                changed("Synchronize secret community macro")
                current = state["macro"]
                self.client.call("usermacro.update" if current else "usermacro.create",
                    {**macro, **({"hostmacroid": current["hostmacroid"]} if current else {"hostid": hostid})})
        interfaces = self.client.call("hostinterface.get", {"output": ["interfaceid", "type"], "hostids": [hostid], "filter": {"type": 2}})
        if len(interfaces) != 1:
            raise MonitoringError("zabbix_ambiguous_snmp_interface", 409)
        interfaceid = interfaces[0]["interfaceid"]
        if not state["item"]:
            changed("Create sysObjectID verification item")
            self.client.call("item.create", {"hostid": hostid, "name": "Atlas SNMP sysObjectID verification", "key_": KEY,
                "type": 20, "value_type": 1, "interfaceid": interfaceid, "snmp_oid": OID, "delay": "30s", "status": 0})
        elif "Reconcile sysObjectID verification item" in actions:
            changed("Reconcile sysObjectID verification item")
            self.client.call("item.update", {"itemid": state["item"]["itemid"], "interfaceid": interfaceid, "status": 0})
        return hostid

    def verify(self, since):
        state = self.inspect()
        if not state["host"] or not state["item"]:
            return "PENDING"
        item = state["item"]
        snmp = [i for i in state["host"]["interfaces"] if str(i.get("type")) == "2"]
        if (len(snmp) == 1 and str(snmp[0].get("available")) == "1"
                and str(item.get("state")) == "0" and int(item.get("lastclock", 0)) >= since
                and str(item.get("lastvalue", "")).strip()):
            return "VERIFIED"
        return "PENDING"
