#!/usr/bin/env python3

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml


ROOT = Path(__file__).resolve().parent.parent

BASELINE_DIR = (
    ROOT
    / "catalog"
    / "baseline"
)

CHOICE_SETS_FILE = (
    BASELINE_DIR
    / "custom_field_choice_sets.yaml"
)

CUSTOM_FIELDS_FILE = (
    BASELINE_DIR
    / "custom_fields.yaml"
)

NETBOX_URL = os.getenv(
    "NETBOX_URL",
    "http://localhost:8000",
).rstrip("/")

NETBOX_TOKEN = os.getenv(
    "NETBOX_TOKEN"
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Provision Atlas NetBox baseline"
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply missing baseline objects",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class NetBoxClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": (
                f"Bearer {token}"
            ),
            "Accept": "application/json",
            "Content-Type": (
                "application/json"
            ),
        })

    def _url(self, endpoint):
        return urljoin(
            f"{self.url}/",
            endpoint.lstrip("/"),
        )

    def get(self, endpoint, params=None):
        response = self.session.get(
            self._url(endpoint),
            params=params,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

    def get_all(self, endpoint):
        url = self._url(endpoint)

        params = {
            "limit": 1000,
        }

        results = []

        while url:
            response = self.session.get(
                url,
                params=params,
                timeout=30,
            )

            response.raise_for_status()

            data = response.json()

            results.extend(
                data.get(
                    "results",
                    []
                )
            )

            url = data.get("next")

            params = None

        return results

    def post(self, endpoint, payload):
        response = self.session.post(
            self._url(endpoint),
            json=payload,
            timeout=30,
        )

        if not response.ok:
            print()
            print(
                f"[ERROR] POST {endpoint}"
            )

            print(
                f"HTTP "
                f"{response.status_code}"
            )

            try:
                print(
                    yaml.safe_dump(
                        response.json(),
                        sort_keys=False,
                    ).rstrip()
                )

            except Exception:
                print(response.text)

            print()
            print("Payload:")

            print(
                yaml.safe_dump(
                    payload,
                    sort_keys=False,
                ).rstrip()
            )

            response.raise_for_status()

        return response.json()

    def status(self):
        return self.get(
            "/api/status/"
        )


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def load_yaml(path):
    if not path.exists():
        raise RuntimeError(
            f"Missing baseline file: "
            f"{path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:

        data = yaml.safe_load(
            handle
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid YAML root: {path}"
        )

    return data


def load_baseline():
    choice_data = load_yaml(
        CHOICE_SETS_FILE
    )

    custom_field_data = load_yaml(
        CUSTOM_FIELDS_FILE
    )

    choice_sets = choice_data.get(
        "choice_sets",
        []
    )

    custom_fields = (
        custom_field_data.get(
            "custom_fields",
            []
        )
    )

    if not isinstance(
        choice_sets,
        list,
    ):
        raise RuntimeError(
            "choice_sets must be a list"
        )

    if not isinstance(
        custom_fields,
        list,
    ):
        raise RuntimeError(
            "custom_fields must be a list"
        )

    return (
        choice_sets,
        custom_fields,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_unique_names(
    objects,
    object_name,
):
    names = []

    for obj in objects:
        name = obj.get("name")

        if not name:
            raise RuntimeError(
                f"{object_name} without name"
            )

        names.append(name)

    duplicates = {
        name
        for name in names
        if names.count(name) > 1
    }

    if duplicates:
        raise RuntimeError(
            f"Duplicate {object_name} "
            f"name(s): "
            f"{sorted(duplicates)}"
        )


def validate_baseline(
    choice_sets,
    custom_fields,
):
    validate_unique_names(
        choice_sets,
        "Choice Set",
    )

    validate_unique_names(
        custom_fields,
        "Custom Field",
    )

    choice_names = {
        obj["name"]
        for obj in choice_sets
    }

    for custom_field in custom_fields:
        field_type = custom_field.get(
            "type"
        )

        choice_set = (
            custom_field.get(
                "choice_set"
            )
        )

        if field_type in {
            "select",
            "multiselect",
        }:
            if not choice_set:
                raise RuntimeError(
                    f"Custom Field "
                    f"{custom_field['name']} "
                    f"requires choice_set"
                )

            if choice_set not in (
                choice_names
            ):
                raise RuntimeError(
                    f"Custom Field "
                    f"{custom_field['name']} "
                    f"references unknown "
                    f"Choice Set "
                    f"'{choice_set}'"
                )

        elif choice_set:
            raise RuntimeError(
                f"Custom Field "
                f"{custom_field['name']} "
                f"has a Choice Set but type "
                f"is '{field_type}'"
            )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

CHOICE_VALUE_FIELDS = {
    "type",
    "filter_logic",
    "ui_visible",
    "ui_editable",
}


def normalize_custom_field(
    obj,
):
    normalized = {}

    for key, value in obj.items():

        if (
            key
            in CHOICE_VALUE_FIELDS
            and
            isinstance(value, dict)
        ):
            normalized[key] = (
                value.get("value")
            )

            continue

        if key == "choice_set":
            if isinstance(value, dict):
                normalized[key] = (
                    value.get("name")
                )
            else:
                normalized[key] = None

            continue

        normalized[key] = value

    return normalized


# ---------------------------------------------------------------------------
# Drift comparison
# ---------------------------------------------------------------------------

def compare_declared_fields(
    desired,
    existing,
):
    drift = {}

    for key, wanted in desired.items():
        current = existing.get(key)

        if current != wanted:
            drift[key] = {
                "desired": wanted,
                "current": current,
            }

    return drift


def print_drift(
    object_type,
    name,
    drift,
):
    print(
        f"[DRIFT] {object_type}: "
        f"{name}"
    )

    for field, values in (
        sorted(drift.items())
    ):
        print(
            f"        {field}:"
        )

        print(
            f"          desired: "
            f"{values['desired']!r}"
        )

        print(
            f"          current: "
            f"{values['current']!r}"
        )


# ---------------------------------------------------------------------------
# Choice Sets
# ---------------------------------------------------------------------------

def provision_choice_sets(
    client,
    desired_choice_sets,
    apply_changes,
):
    endpoint = (
        "/api/extras/"
        "custom-field-choice-sets/"
    )

    existing_objects = (
        client.get_all(endpoint)
    )

    existing_by_name = {
        obj["name"]: obj
        for obj in existing_objects
    }

    existing_count = 0
    created_count = 0
    missing_count = 0
    drift_count = 0

    print()
    print(
        "Custom Field Choice Sets"
    )
    print("-" * 72)

    for desired in (
        desired_choice_sets
    ):
        name = desired["name"]

        existing = (
            existing_by_name.get(name)
        )

        if existing is None:
            missing_count += 1

            print(
                f"[CREATE] {name}"
            )

            if apply_changes:
                created = client.post(
                    endpoint,
                    desired,
                )

                existing_by_name[
                    name
                ] = created

                created_count += 1

                print(
                    f"[CREATED] {name} "
                    f"(id={created['id']})"
                )

            continue

        existing_count += 1

        drift = (
            compare_declared_fields(
                desired,
                existing,
            )
        )

        if drift:
            drift_count += 1

            print_drift(
                "Choice Set",
                name,
                drift,
            )

        else:
            print(
                f"[OK] {name}"
            )

    # Refresh after creation so IDs are known.
    if apply_changes and created_count:
        existing_objects = (
            client.get_all(endpoint)
        )

        existing_by_name = {
            obj["name"]: obj
            for obj in existing_objects
        }

    return {
        "objects": existing_by_name,
        "existing": existing_count,
        "missing": missing_count,
        "created": created_count,
        "drift": drift_count,
    }


# ---------------------------------------------------------------------------
# Custom Fields
# ---------------------------------------------------------------------------

def build_custom_field_payload(
    desired,
    choice_sets,
):
    payload = dict(desired)

    choice_set_name = (
        payload.pop(
            "choice_set",
            None,
        )
    )

    if choice_set_name:
        choice_set = (
            choice_sets.get(
                choice_set_name
            )
        )

        if not choice_set:
            raise RuntimeError(
                f"Choice Set "
                f"'{choice_set_name}' "
                f"does not exist in NetBox"
            )

        payload[
            "choice_set"
        ] = choice_set["id"]

    return payload


def provision_custom_fields(
    client,
    desired_custom_fields,
    choice_sets,
    apply_changes,
):
    endpoint = (
        "/api/extras/custom-fields/"
    )

    existing_objects = (
        client.get_all(endpoint)
    )

    existing_by_name = {
        obj["name"]: obj
        for obj in existing_objects
    }

    existing_count = 0
    missing_count = 0
    created_count = 0
    drift_count = 0

    print()
    print("Custom Fields")
    print("-" * 72)

    for desired in (
        desired_custom_fields
    ):
        name = desired["name"]

        existing = (
            existing_by_name.get(name)
        )

        if existing is None:
            missing_count += 1

            print(
                f"[CREATE] {name}"
            )

            if apply_changes:
                payload = (
                    build_custom_field_payload(
                        desired,
                        choice_sets,
                    )
                )

                created = client.post(
                    endpoint,
                    payload,
                )

                existing_by_name[
                    name
                ] = created

                created_count += 1

                print(
                    f"[CREATED] {name} "
                    f"(id={created['id']})"
                )

            continue

        existing_count += 1

        normalized = (
            normalize_custom_field(
                existing
            )
        )

        drift = (
            compare_declared_fields(
                desired,
                normalized,
            )
        )

        if drift:
            drift_count += 1

            print_drift(
                "Custom Field",
                name,
                drift,
            )

        else:
            print(
                f"[OK] {name}"
            )

    return {
        "existing": existing_count,
        "missing": missing_count,
        "created": created_count,
        "drift": drift_count,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if not NETBOX_TOKEN:
        sys.exit(
            "[ERROR] NETBOX_TOKEN "
            "environment variable is not set"
        )

    try:
        (
            choice_sets,
            custom_fields,
        ) = load_baseline()

        validate_baseline(
            choice_sets,
            custom_fields,
        )

    except Exception as exc:
        sys.exit(
            f"[ERROR] Baseline validation "
            f"failed: {exc}"
        )

    client = NetBoxClient(
        NETBOX_URL,
        NETBOX_TOKEN,
    )

    try:
        status = client.status()

    except Exception as exc:
        sys.exit(
            f"[ERROR] Cannot connect to "
            f"NetBox: {exc}"
        )

    print("Atlas NetBox Baseline")
    print("=" * 72)

    print(
        f"NetBox: "
        f"{status.get('netbox-version')}"
    )

    print(
        f"Mode:   "
        f"{'APPLY' if args.apply else 'DRY-RUN'}"
    )

    print()

    print(
        f"Declared Choice Sets: "
        f"{len(choice_sets)}"
    )

    print(
        f"Declared Custom Fields: "
        f"{len(custom_fields)}"
    )

    try:
        choice_summary = (
            provision_choice_sets(
                client,
                choice_sets,
                args.apply,
            )
        )

        custom_field_summary = (
            provision_custom_fields(
                client,
                custom_fields,
                choice_summary["objects"],
                args.apply,
            )
        )

    except Exception as exc:
        sys.exit(
            f"\n[ERROR] Baseline "
            f"provisioning stopped: {exc}"
        )

    print()
    print("=" * 72)

    if args.apply:
        print("APPLY SUMMARY")
    else:
        print("DRY-RUN SUMMARY")

    print("=" * 72)

    print(
        "Choice Sets:"
    )

    print(
        f"  existing: "
        f"{choice_summary['existing']}"
    )

    print(
        f"  missing:  "
        f"{choice_summary['missing']}"
    )

    if args.apply:
        print(
            f"  created:  "
            f"{choice_summary['created']}"
        )

    print(
        f"  drift:    "
        f"{choice_summary['drift']}"
    )

    print()

    print(
        "Custom Fields:"
    )

    print(
        f"  existing: "
        f"{custom_field_summary['existing']}"
    )

    print(
        f"  missing:  "
        f"{custom_field_summary['missing']}"
    )

    if args.apply:
        print(
            f"  created:  "
            f"{custom_field_summary['created']}"
        )

    print(
        f"  drift:    "
        f"{custom_field_summary['drift']}"
    )

    total_drift = (
        choice_summary["drift"]
        +
        custom_field_summary["drift"]
    )

    print()

    if total_drift:
        print(
            "[ERROR] Existing NetBox "
            "configuration differs from "
            "the Atlas baseline."
        )

        print(
            "Existing objects were NOT "
            "automatically modified."
        )

        raise SystemExit(2)

    if args.apply:
        print(
            "[OK] Atlas baseline provisioning "
            "completed."
        )
    else:
        print(
            "No changes were made to NetBox."
        )


if __name__ == "__main__":
    main()
