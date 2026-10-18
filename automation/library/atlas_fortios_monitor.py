#!/usr/bin/python
"""Read one allow-listed FortiOS monitor endpoint over Ansible httpapi."""

from __future__ import annotations

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.connection import Connection


ALLOWED_PATHS = {"/api/v2/monitor/web-ui/state"}


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            "access_token": {
                "type": "str",
                "required": True,
                "no_log": True,
            },
            "path": {
                "type": "str",
                "required": True,
                "choices": sorted(ALLOWED_PATHS),
            },
        },
        supports_check_mode=True,
    )
    if not module._socket_path:
        module.fail_json(msg="FortiOS httpapi connection is unavailable")

    connection = Connection(module._socket_path)
    connection.set_custom_option("access_token", module.params["access_token"])
    try:
        status, body = connection.send_request(
            url=module.params["path"],
            method="GET",
        )
    except Exception as exc:
        module.fail_json(
            msg="FortiOS monitor request failed",
            error_type=type(exc).__name__,
        )

    if status != 200:
        module.fail_json(
            msg="FortiOS monitor request returned an unexpected HTTP status",
            http_status=status,
        )
    try:
        response = json.loads(body)
    except (TypeError, ValueError):
        module.fail_json(msg="FortiOS monitor response is not valid JSON")
    if not isinstance(response, dict):
        module.fail_json(msg="FortiOS monitor response has an invalid schema")

    module.exit_json(changed=False, response=response)


if __name__ == "__main__":
    main()
