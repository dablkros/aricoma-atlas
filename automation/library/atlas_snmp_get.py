#!/usr/bin/python
"""Minimal, read-only SNMP GET module for Atlas allow-listed OIDs."""

from __future__ import annotations

import asyncio

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: atlas_snmp_get
short_description: Read an explicit map of SNMPv2c scalar OIDs
options:
  host:
    type: str
    required: true
  port:
    type: int
    default: 161
  community:
    type: str
    required: true
  queries:
    type: dict
    required: true
  timeout:
    type: int
    default: 3
  retries:
    type: int
    default: 1
"""


async def _read(params):
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
    )

    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(
            (params["host"], params["port"]),
            timeout=params["timeout"],
            retries=params["retries"],
        )
        names = list(params["queries"])
        objects = [
            ObjectType(ObjectIdentity(params["queries"][name]))
            for name in names
        ]
        error_indication, error_status, error_index, var_binds = await get_cmd(
            engine,
            CommunityData(params["community"], mpModel=1),
            target,
            ContextData(),
            *objects,
        )
        if error_indication:
            code = (
                "snmp_timeout"
                if "timeout" in str(error_indication).lower()
                else "snmp_transport_failed"
            )
            raise RuntimeError(code)
        if error_status:
            raise RuntimeError("snmp_agent_error")
        results = {}
        for name, (_oid, value) in zip(names, var_binds, strict=True):
            type_name = value.__class__.__name__
            if type_name in {"NoSuchObject", "NoSuchInstance", "EndOfMibView"}:
                results[name] = {
                    "status": {
                        "NoSuchObject": "no_such_object",
                        "NoSuchInstance": "no_such_instance",
                        "EndOfMibView": "end_of_mib",
                    }[type_name]
                }
                continue
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                normalized = value.prettyPrint()
            results[name] = {"status": "ok", "value": normalized}
        return results
    finally:
        engine.close_dispatcher()


def main():
    module = AnsibleModule(
        argument_spec={
            "host": {"type": "str", "required": True},
            "port": {"type": "int", "default": 161},
            "community": {"type": "str", "required": True, "no_log": True},
            "queries": {"type": "dict", "required": True},
            "timeout": {"type": "int", "default": 3},
            "retries": {"type": "int", "default": 1},
        },
        supports_check_mode=True,
    )
    try:
        values = asyncio.run(_read(module.params))
    except Exception as exc:
        code = str(exc) if str(exc) in {
            "snmp_timeout",
            "snmp_transport_failed",
            "snmp_agent_error",
        } else "snmp_execution_failed"
        module.fail_json(msg=code)
    module.exit_json(changed=False, values=values)


if __name__ == "__main__":
    main()
