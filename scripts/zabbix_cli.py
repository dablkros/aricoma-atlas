#!/usr/bin/env python3
"""Secret-free operator commands for the Atlas Zabbix API boundary."""

import argparse
import json
import sys

import requests


API_BASE = "http://127.0.0.1:8081/api"


def request(method: str, path: str) -> dict:
    try:
        response = requests.request(
            method,
            f"{API_BASE}{path}",
            timeout=(3, 120),
        )
    except requests.RequestException:
        raise RuntimeError("Atlas backend is unavailable") from None
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("Atlas backend returned invalid data") from None
    if not response.ok:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise RuntimeError(str(code or "zabbix_operation_failed"))
    if not isinstance(body, dict):
        raise RuntimeError("Atlas backend returned invalid data")
    return body


def print_status() -> int:
    body = request("GET", "/zabbix/status")
    print(f"state: {str(body.get('status', 'unknown')).upper()}")
    print(f"version: {body.get('version', 'unknown')}")
    print(f"managed_hosts: {body.get('managed_hosts', 'unknown')}")
    return 0


def print_sync() -> int:
    body = request("POST", "/zabbix/sync")
    summary = body.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("Atlas backend returned invalid data")
    print(f"status: {body.get('status', 'unknown')}")
    for field in ("created", "updated", "disabled", "unchanged", "errors"):
        print(f"{field}: {summary.get(field, 'unknown')}")
    for issue in body.get("issues", []):
        if isinstance(issue, dict):
            print(
                "issue: "
                + json.dumps(
                    {"device": issue.get("device"), "code": issue.get("code")},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
    return 1 if int(summary.get("errors", 0)) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "sync"))
    args = parser.parse_args()
    return print_status() if args.command == "status" else print_sync()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TypeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
