#!/usr/bin/python
"""Apply Atlas' existing FortiOS 7.0.9 SNMP contract over Ansible httpapi."""

from __future__ import annotations

import json
from urllib.parse import quote, urlencode

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.connection import Connection


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "access_token": {"type": "str", "required": True, "no_log": True},
            "operation": {"type": "str", "choices": ["inspect", "configure"], "required": True},
            "vdom": {"type": "str", "required": True},
            "interface": {"type": "str", "required": True},
            "community": {"type": "str", "required": True, "no_log": True},
            "community_id": {"type": "int", "required": True},
            "snmp_port": {"type": "int", "required": True},
            "source_ips": {"type": "list", "elements": "str", "required": True},
        },
        supports_check_mode=False,
    )
    if not module._socket_path:
        module.fail_json(msg="FortiOS httpapi connection is unavailable")

    params = module.params
    connection = Connection(module._socket_path)
    connection.set_custom_option("access_token", params["access_token"])

    def request(method: str, resource: str, payload=None):
        query = urlencode({"vdom": params["vdom"]})
        url = f"/api/v2/cmdb/{resource}?{query}"
        try:
            status, body = connection.send_request(
                url=url,
                data=json.dumps(payload) if payload is not None else None,
                method=method,
            )
        except Exception as exc:
            module.fail_json(
                msg="FortiOS CMDB request failed",
                error_type=type(exc).__name__,
            )
        if not 200 <= status < 300:
            module.fail_json(msg="FortiOS CMDB request returned an unexpected status", http_status=status)
        try:
            response = json.loads(body)
        except (TypeError, ValueError):
            module.fail_json(msg="FortiOS CMDB response is not valid JSON")
        if not isinstance(response, dict) or response.get("status") != "success":
            module.fail_json(msg="FortiOS CMDB response has an invalid schema")
        if str(response.get("version", "")).lstrip("v") != "7.0.9":
            module.fail_json(msg="unsupported_fortios_version")
        return response.get("results")

    def one(value):
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not isinstance(value, dict):
            module.fail_json(msg="FortiOS CMDB object has an invalid schema")
        return value

    desired = {
        "id": params["community_id"],
        "name": params["community"],
        "status": "enable",
        "query-v1-status": "disable",
        "query-v2c-status": "enable",
        "query-v2c-port": params["snmp_port"],
        "trap-v1-status": "disable",
        "trap-v2c-status": "disable",
        "hosts6": [],
        "hosts": [
            {"id": index + 1, "ip": f"{address} 255.255.255.255", "host-type": "query"}
            for index, address in enumerate(params["source_ips"])
        ],
    }

    def community_equal(actual):
        for key, value in desired.items():
            if key == "hosts":
                current = sorted((item.get("ip"), item.get("host-type")) for item in actual.get("hosts", []))
                expected = sorted((item["ip"], item["host-type"]) for item in value)
                if current != expected:
                    return False
            elif actual.get(key) != value:
                return False
        return True

    def state():
        info = one(request("GET", "system.snmp/sysinfo"))
        communities = request("GET", "system.snmp/community")
        interface = one(request("GET", "system/interface/" + quote(params["interface"], safe="")))
        if not isinstance(communities, list) or any(not isinstance(item, dict) for item in communities):
            module.fail_json(msg="FortiOS SNMP communities have an invalid schema")
        access = interface.get("allowaccess")
        if not isinstance(access, str):
            module.fail_json(msg="FortiOS interface allowaccess has an invalid schema")
        owned = [item for item in communities if item.get("id") == params["community_id"]]
        if len(owned) > 1 or (owned and owned[0].get("name") != params["community"]):
            module.fail_json(msg="snmp_community_conflict")
        if any(item.get("name") == params["community"] and item.get("id") != params["community_id"] for item in communities):
            module.fail_json(msg="snmp_community_conflict")
        compliant = (
            info.get("status") == "enable"
            and "snmp" in access.split()
            and len(owned) == 1
            and community_equal(owned[0])
        )
        return compliant, info, interface, owned

    compliant, info, interface, owned = state()
    if params["operation"] == "inspect":
        module.exit_json(changed=False, compliant=compliant, actions=[])

    actions = []
    if not owned or not community_equal(owned[0]):
        resource = "system.snmp/community" + (f"/{params['community_id']}" if owned else "")
        request("PUT" if owned else "POST", resource, desired)
        actions.append("Configure Atlas SNMP community")
    if info.get("status") != "enable":
        request("PUT", "system.snmp/sysinfo", {"status": "enable"})
        actions.append("Enable SNMP agent")
    access = interface["allowaccess"].split()
    if "snmp" not in access:
        request(
            "PUT",
            "system/interface/" + quote(params["interface"], safe=""),
            {"allowaccess": " ".join([*access, "snmp"])},
        )
        actions.append("Add SNMP to management interface access")
    verified, _info, _interface, _owned = state()
    if not verified:
        module.fail_json(msg="fortigate_configuration_verification_failed")
    module.exit_json(changed=bool(actions), compliant=True, actions=actions)


if __name__ == "__main__":
    main()
