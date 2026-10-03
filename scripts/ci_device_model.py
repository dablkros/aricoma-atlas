#!/usr/bin/env python3
"""Verify the real NetBox API contract in the disposable Atlas integration job."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlas.openbao_client import OpenBaoClient
from atlas.platforms import default_platform, load_platform_config
from atlas.device_credentials import resolve_oxidized_device, DeviceValidationError, validate_snmp_secret
from scripts.provision_netbox import NetBoxClient, load_catalog, related_id

SNAPSHOT = ROOT / ".runtime/device-model-ci.json"


def snapshot(client, config):
    platforms = {p["slug"]: p for p in client.get_all("/api/dcim/platforms/")}
    for slug in config["platforms"]:
        assert slug in platforms, f"Missing platform: {slug}"
    fields = [f for f in client.get_all("/api/extras/custom-fields/") if f["name"] == "credential_profile"]
    assert len(fields) == 1 and fields[0]["type"]["value"] == "text", "Invalid credential_profile field"
    types = {(t["manufacturer"]["name"], t["model"]): t for t in client.get_all("/api/dcim/device-types/")}
    items, errors = load_catalog()
    assert not errors
    for item in items:
        slug = default_platform(item["data"], config)
        expected = platforms[slug]["id"] if slug else None
        actual = types[item["manufacturer"], item["model"]]
        assert related_id(actual.get("default_platform")) == expected, f"Wrong default platform: {item['model']}"
    return {"platforms": {k: platforms[k]["id"] for k in config["platforms"]},
            "credential_field_id": fields[0]["id"],
            "types": {f"{v}/{m}": t["id"] for (v, m), t in types.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "verify"))
    args = parser.parse_args()
    bao = OpenBaoClient()
    token = bao.login_from_identity(ROOT / ".runtime/openbao-approle.json")
    api = bao.kv_read(token, "netbox/api")["token"]
    client = NetBoxClient("http://127.0.0.1:8000", api)
    config = load_platform_config()
    state = snapshot(client, config)
    if args.action == "seed":
        site = client.post("/api/dcim/sites/", {"name": "CI device model", "slug": "ci-device-model"})
        role = client.post("/api/dcim/device-roles/", {"name": "CI device model", "slug": "ci-device-model", "color": "9e9e9e"})
        types = client.get_all("/api/dcim/device-types/")
        dt = next(t for t in types if t["slug"] == "cisco-c9500-48y4c")
        common = {"site": site["id"], "role": role["id"], "device_type": dt["id"], "status": "active",
                  "custom_fields": {"credential_profile": "ci-device-model", "oxidized_enabled": True}}
        inherited = client.post("/api/dcim/devices/", {**common, "name": "CI-INHERITED"})
        override = client.post("/api/dcim/devices/", {**common, "name": "CI-OVERRIDE", "platform": state["platforms"]["cisco-ios"]})
        assert related_id(inherited["platform"]) == state["platforms"]["cisco-ios-xe"]
        state["devices"] = {str(d["id"]): related_id(d["platform"]) for d in (inherited, override)}
        SNAPSHOT.write_text(json.dumps(state), encoding="utf-8")
        # Ephemeral fixture values only. Never print secret-bearing objects.
        bao.kv_write(token, "devices/credentials/cisco/ci-device-model/admin", {"username": "ci-fixture", "password": "ci-only-ephemeral"})
        bao.kv_write(token, "devices/credentials/cisco/ci-device-model/snmp", {"version": "2c", "community": "ci-only-ephemeral"})
    else:
        before = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        expected_devices = before.pop("devices")
        assert state == before, "Platform/type/field identity changed on redeploy"
        for pk, platform_id in expected_devices.items():
            d = client.get(f"/api/dcim/devices/{pk}/")
            assert related_id(d["platform"]) == platform_id, "Manual Device platform override changed"
            assert d["custom_fields"]["credential_profile"] == "ci-device-model"
            # Use the actual REST serializer; temporary in-memory IP for resolver validation.
            d["primary_ip4"] = {"address": "192.0.2.1/32"}
            resolved = resolve_oxidized_device(d, bao, token)
            assert resolved.reference.model == "ios"
            d["custom_fields"]["credential_profile"] = "ci-nonexistent"
            try:
                resolve_oxidized_device(d, bao, token)
            except DeviceValidationError as exc:
                assert str(exc) == "Credential profile not found"
            else:
                raise AssertionError("Missing credential unexpectedly resolved")
        validate_snmp_secret(bao.kv_read(token, "devices/credentials/cisco/ci-device-model/snmp"))
    print(f"[OK] Device model {args.action}: platforms, catalog defaults, credential field and inheritance")


if __name__ == "__main__":
    main()
