#!/usr/bin/env python3

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atlas.platforms import (load_platform_config, provision_platforms, enrich_device_types,
                             catalog_platform_report, print_platform_report, default_platform)

CATALOG = ROOT / "build" / "device-types"

NETBOX_URL = os.getenv(
    "NETBOX_URL",
    "http://localhost:8000",
).rstrip("/")

NETBOX_TOKEN = os.getenv("NETBOX_TOKEN")

TESTED_NETBOX_VERSION = "4.7.1"

DEFAULT_BATCH_SIZE = 200


# ===========================================================================
# Device Type schema
# ===========================================================================

DEVICE_TYPE_FIELDS = {
    "model",
    "slug",
    "part_number",
    "u_height",
    "is_full_depth",
    "airflow",
    "subdevice_role",
    "weight",
    "weight_unit",
    "description",
    "comments",
}


IGNORED_DEVICE_TYPE_FIELDS = {
    # Device Type Library metadata.
    # These are NOT sent as JSON fields to NetBox DeviceType.
    "default_platform",  # Resolved to a native FK by enrichment, never sent as a slug.
    "front_image",
    "rear_image",
    "is_powered",
}


COMPONENT_SPECS = {
    "console-ports": {
        "endpoint": "/api/dcim/console-port-templates/",
        "label": "Console ports",
        "fields": {
            "name",
            "label",
            "type",
            "description",
        },
    },

    "console-server-ports": {
        "endpoint": "/api/dcim/console-server-port-templates/",
        "label": "Console server ports",
        "fields": {
            "name",
            "label",
            "type",
        },
    },

    "power-ports": {
        "endpoint": "/api/dcim/power-port-templates/",
        "label": "Power ports",
        "fields": {
            "name",
            "label",
            "type",
            "maximum_draw",
            "allocated_draw",
            "description",
        },
    },

    "interfaces": {
        "endpoint": "/api/dcim/interface-templates/",
        "label": "Interfaces",
        "fields": {
            "name",
            "label",
            "type",
            "mgmt_only",
            "poe_mode",
            "poe_type",
            "enabled",
            "description",
        },
    },

    "rear-ports": {
        "endpoint": "/api/dcim/rear-port-templates/",
        "label": "Rear ports",
        "fields": {
            "name",
            "label",
            "type",
            "positions",
            "description",
        },
    },

    "module-bays": {
        "endpoint": "/api/dcim/module-bay-templates/",
        "label": "Module bays",
        "fields": {
            "name",
            "label",
            "position",
            "description",
        },
    },

    "device-bays": {
        "endpoint": "/api/dcim/device-bay-templates/",
        "label": "Device bays",
        "fields": {
            "name",
            "label",
        },
    },

    # Dependencies: power-port must exist first.
    "power-outlets": {
        "endpoint": "/api/dcim/power-outlet-templates/",
        "label": "Power outlets",
        "fields": {
            "name",
            "label",
            "type",
            "feed_leg",
            "power_port",
        },
    },

    # Currently zero in Atlas Catalog 0.1-beta,
    # but supporting this costs almost nothing.
    "front-ports": {
        "endpoint": "/api/dcim/front-port-templates/",
        "label": "Front ports",
        "fields": {
            "name",
            "label",
            "type",
            "rear_port",
            "rear_port_position",
        },
    },
}


COMPONENT_ORDER = [
    "console-ports",
    "console-server-ports",
    "power-ports",
    "interfaces",
    "rear-ports",
    "module-bays",
    "device-bays",

    # Dependency-based components last.
    "power-outlets",
    "front-ports",
]


UNSUPPORTED_COMPONENTS = {
    "inventory-items",
    "cooling-intakes",
    "cooling-outflows",
}


INTERNAL_COMPONENT_FIELDS = {
    "_is_power_source",
}


KNOWN_TOP_LEVEL_FIELDS = (
    {
        "manufacturer",
        *DEVICE_TYPE_FIELDS,
        *IGNORED_DEVICE_TYPE_FIELDS,
        *COMPONENT_SPECS.keys(),
        *UNSUPPORTED_COMPONENTS,
    }
)


# ===========================================================================
# CLI
# ===========================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Atlas NetBox Provisioner"
    )

    target = parser.add_mutually_exclusive_group()

    target.add_argument(
        "--all",
        action="store_true",
        help="Process the complete Atlas Device Catalog",
    )

    target.add_argument(
        "--device-type",
        help=(
            "Process one Device Type using its YAML filename stem, "
            "for example FG-60F"
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to NetBox",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Maximum objects per REST bulk-create request "
            f"(default: {DEFAULT_BATCH_SIZE})"
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print individual object names",
    )

    return parser.parse_args()


# ===========================================================================
# API client
# ===========================================================================

class NetBoxClient:
    def __init__(self, url, token):
        self.url = url.rstrip("/")

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
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
            timeout=60,
        )

        response.raise_for_status()

        return response.json()

    def get_all(self, endpoint):
        url = self._url(endpoint)

        params = {
            "limit": 1000,
        }

        objects = []

        while url:
            response = self.session.get(
                url,
                params=params,
                timeout=60,
            )

            response.raise_for_status()

            data = response.json()

            objects.extend(
                data.get("results", [])
            )

            url = data.get("next")

            # NetBox's next URL already contains pagination parameters.
            params = None

        return objects

    def post(self, endpoint, payload):
        response = self.session.post(
            self._url(endpoint),
            json=payload,
            timeout=60,
        )

        if not response.ok:
            self._print_error(
                endpoint,
                payload,
                response,
            )

            response.raise_for_status()

        return response.json()

    def bulk_post(
        self,
        endpoint,
        payloads,
        batch_size,
        metadata=None,
    ):
        """
        Bulk-create objects in batches.

        NetBox bulk creation is atomic per request. If a batch fails,
        prior successful batches remain committed, while the failed
        batch is rolled back.

        Because provisioning is idempotent, the script can be fixed
        and safely re-run.
        """

        created = []

        metadata = metadata or [
            None
            for _ in payloads
        ]

        for offset in range(
            0,
            len(payloads),
            batch_size,
        ):
            batch = payloads[
                offset:offset + batch_size
            ]

            batch_meta = metadata[
                offset:offset + batch_size
            ]

            batch_number = (
                offset // batch_size
            ) + 1

            print(
                f"    batch {batch_number}: "
                f"{len(batch)} object(s)"
            )

            response = self.session.post(
                self._url(endpoint),
                json=batch,
                timeout=120,
            )

            if not response.ok:
                print()
                print(
                    f"[ERROR] Bulk POST failed:"
                    f" {endpoint}"
                )

                print(
                    f"HTTP {response.status_code}"
                )

                try:
                    error_data = response.json()

                    print(
                        yaml.safe_dump(
                            error_data,
                            sort_keys=False,
                        ).rstrip()
                    )

                    # NetBox 4.7 returns offending indexes
                    # for bulk validation errors.
                    errors = error_data.get(
                        "errors",
                        []
                    )

                    for error in errors:
                        index = error.get(
                            "index"
                        )

                        if (
                            isinstance(index, int)
                            and
                            index < len(batch_meta)
                        ):
                            print()
                            print(
                                "Offending source object:"
                            )

                            print(
                                batch_meta[index]
                            )

                except Exception:
                    print(response.text)

                print()

                raise RuntimeError(
                    f"Bulk creation failed for "
                    f"{endpoint}"
                )

            result = response.json()

            if not isinstance(result, list):
                raise RuntimeError(
                    f"Expected list response from "
                    f"bulk POST {endpoint}"
                )

            created.extend(result)

        return created

    def patch(self, endpoint, payload):
        response = self.session.patch(self._url(endpoint), json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    def status(self):
        return self.get(
            "/api/status/"
        )

    @staticmethod
    def _print_error(
        endpoint,
        payload,
        response,
    ):
        print()
        print(
            f"[ERROR] POST {endpoint} "
            f"failed with HTTP "
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


# ===========================================================================
# General helpers
# ===========================================================================

def make_slug(value):
    slug = value.strip().lower()

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        slug,
    )

    return slug.strip("-")


def related_id(value):
    if isinstance(value, int):
        return value

    if isinstance(value, dict):
        return value.get("id")

    return None


def chunk_description(item, component=None):
    if component is None:
        return (
            f"{item['manufacturer']} / "
            f"{item['model']} "
            f"({item['file'].name})"
        )

    return (
        f"{item['manufacturer']} / "
        f"{item['model']} / "
        f"{component.get('name', '<unnamed>')} "
        f"({item['file'].name})"
    )


# ===========================================================================
# Catalog loading & validation
# ===========================================================================

def load_catalog():
    items = []
    errors = []

    for vendor_dir in sorted(
        CATALOG.iterdir()
    ):
        if not vendor_dir.is_dir():
            continue

        for yaml_file in sorted(
            vendor_dir.glob("*.yaml")
        ):
            try:
                with yaml_file.open(
                    "r",
                    encoding="utf-8",
                ) as handle:

                    data = yaml.safe_load(
                        handle
                    )

                if not isinstance(
                    data,
                    dict,
                ):
                    raise ValueError(
                        "YAML root is not an object"
                    )

                manufacturer = data.get(
                    "manufacturer"
                )

                model = data.get(
                    "model"
                )

                slug = data.get(
                    "slug"
                )

                if not manufacturer:
                    raise ValueError(
                        "manufacturer is missing"
                    )

                if not model:
                    raise ValueError(
                        "model is missing"
                    )

                if not slug:
                    raise ValueError(
                        "slug is missing"
                    )

                items.append({
                    "file": yaml_file,
                    "filename": yaml_file.stem,
                    "manufacturer": str(
                        manufacturer
                    ),
                    "model": str(model),
                    "slug": str(slug),
                    "data": data,
                })

            except Exception as exc:
                errors.append(
                    f"{yaml_file}: {exc}"
                )

    return items, errors


def validate_item(item):
    errors = []

    data = item["data"]

    # -----------------------------------------------------------------------
    # Unknown top-level keys
    # -----------------------------------------------------------------------

    unknown = (
        set(data.keys())
        - KNOWN_TOP_LEVEL_FIELDS
    )

    for key in sorted(unknown):
        errors.append(
            f"{item['file']}: "
            f"unknown top-level field '{key}'"
        )

    # -----------------------------------------------------------------------
    # Unsupported components
    # -----------------------------------------------------------------------

    for component_type in sorted(
        UNSUPPORTED_COMPONENTS
    ):
        if data.get(component_type):
            errors.append(
                f"{item['file']}: "
                f"component type "
                f"'{component_type}' "
                f"is not supported"
            )

    # -----------------------------------------------------------------------
    # Validate component fields
    # -----------------------------------------------------------------------

    for component_type, spec in (
        COMPONENT_SPECS.items()
    ):
        components = data.get(
            component_type,
            [],
        )

        if not components:
            continue

        if not isinstance(
            components,
            list,
        ):
            errors.append(
                f"{item['file']}: "
                f"{component_type} is not a list"
            )

            continue

        for component in components:
            if not isinstance(
                component,
                dict,
            ):
                errors.append(
                    f"{item['file']}: "
                    f"{component_type} contains "
                    f"a non-object value"
                )

                continue

            if not component.get("name"):
                errors.append(
                    f"{item['file']}: "
                    f"{component_type} component "
                    f"has no name"
                )

            allowed = (
                spec["fields"]
                | INTERNAL_COMPONENT_FIELDS
            )

            unknown_component_fields = (
                set(component.keys())
                - allowed
            )

            for field in sorted(
                unknown_component_fields
            ):
                errors.append(
                    f"{item['file']}: "
                    f"{component_type}/"
                    f"{component.get('name')} "
                    f"contains unsupported field "
                    f"'{field}'"
                )

    # -----------------------------------------------------------------------
    # Validate dependencies inside YAML
    # -----------------------------------------------------------------------

    power_port_names = {
        component["name"]
        for component
        in data.get(
            "power-ports",
            [],
        )
    }

    for outlet in data.get(
        "power-outlets",
        [],
    ):
        power_port = outlet.get(
            "power_port"
        )

        if (
            power_port
            and
            power_port not in power_port_names
        ):
            errors.append(
                f"{item['file']}: "
                f"power outlet "
                f"'{outlet['name']}' references "
                f"unknown power port "
                f"'{power_port}'"
            )

    rear_port_names = {
        component["name"]
        for component
        in data.get(
            "rear-ports",
            [],
        )
    }

    for front_port in data.get(
        "front-ports",
        [],
    ):
        rear_port = front_port.get(
            "rear_port"
        )

        if (
            rear_port
            and
            rear_port not in rear_port_names
        ):
            errors.append(
                f"{item['file']}: "
                f"front port "
                f"'{front_port['name']}' "
                f"references unknown rear port "
                f"'{rear_port}'"
            )

    # -----------------------------------------------------------------------
    # Module Bay Types
    #
    # Atlas Catalog 0.1-beta does not use them.
    # Abort rather than silently discarding them if introduced later.
    # -----------------------------------------------------------------------

    for module_bay in data.get(
        "module-bays",
        [],
    ):
        if (
            "module_bay_types"
            in module_bay
            or
            "module-bay-types"
            in module_bay
        ):
            errors.append(
                f"{item['file']}: "
                f"module bay "
                f"'{module_bay['name']}' "
                f"uses Module Bay Types, "
                f"which are not supported "
                f"by this importer yet"
            )

    return errors


def validate_catalog(items):
    errors = []

    for item in items:
        errors.extend(
            validate_item(item)
        )

    return errors


def select_items(
    items,
    args,
):
    if args.device_type:
        matches = [
            item
            for item in items
            if item["filename"].lower()
            ==
            args.device_type.lower()
        ]

        if not matches:
            raise RuntimeError(
                f"Device Type "
                f"'{args.device_type}' "
                f"not found in Atlas catalog"
            )

        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple Device Types match "
                f"'{args.device_type}'"
            )

        return matches

    if args.all:
        return items

    return None


# ===========================================================================
# Manufacturer provisioning
# ===========================================================================

def provision_manufacturers(
    client,
    items,
    apply_changes,
    verbose,
):
    existing = client.get_all(
        "/api/dcim/manufacturers/"
    )

    by_name = {
        obj["name"]: obj
        for obj in existing
    }

    required = sorted({
        item["manufacturer"]
        for item in items
    })

    missing = [
        name
        for name in required
        if name not in by_name
    ]

    print()
    print("Manufacturers")
    print("-" * 72)

    print(
        f"Existing: {len(required) - len(missing)}"
    )

    print(
        f"Missing:  {len(missing)}"
    )

    if verbose:
        for name in missing:
            print(
                f"  [CREATE] {name}"
            )

    if not apply_changes:
        return by_name

    for name in missing:
        created = client.post(
            "/api/dcim/manufacturers/",
            {
                "name": name,
                "slug": make_slug(name),
            },
        )

        by_name[name] = created

        print(
            f"  [CREATED] {name}"
        )

    return by_name


# ===========================================================================
# Device Type provisioning
# ===========================================================================

def build_device_type_payload(
    item,
    manufacturer_id,
):
    data = item["data"]

    payload = {
        "manufacturer": manufacturer_id,
    }

    for field in DEVICE_TYPE_FIELDS:
        if field in data:
            payload[field] = data[field]

    return payload


def load_device_type_index(
    client,
):
    objects = client.get_all(
        "/api/dcim/device-types/"
    )

    index = {}

    for obj in objects:
        manufacturer = obj.get(
            "manufacturer"
        )

        manufacturer_name = None

        if isinstance(
            manufacturer,
            dict,
        ):
            manufacturer_name = (
                manufacturer.get("name")
            )

        model = obj.get("model")

        if (
            manufacturer_name
            and
            model
        ):
            index[
                (
                    manufacturer_name,
                    model,
                )
            ] = obj

    return index


def provision_device_types(
    client,
    items,
    manufacturers,
    apply_changes,
    batch_size,
    verbose,
    platforms=None,
    platform_config=None,
):
    index = load_device_type_index(
        client
    )

    pending = []
    metadata = []

    existing_count = 0

    for item in items:
        key = (
            item["manufacturer"],
            item["model"],
        )

        if key in index:
            existing_count += 1

            if verbose:
                print(
                    f"  [EXISTS] "
                    f"{item['manufacturer']} / "
                    f"{item['model']}"
                )

            continue

        manufacturer = manufacturers.get(
            item["manufacturer"]
        )

        # Manufacturer may only be planned in dry-run.
        if (
            manufacturer is None
            and
            apply_changes
        ):
            raise RuntimeError(
                f"Manufacturer "
                f"{item['manufacturer']} "
                f"does not exist"
            )

        if apply_changes:
            payload = (
                build_device_type_payload(
                    item,
                    manufacturer["id"],
                )
            )
        else:
            payload = None

        if apply_changes and platform_config is not None:
            slug = default_platform(item["data"], platform_config)
            payload["default_platform"] = platforms[slug]["id"] if slug else None

        pending.append(
            (
                item,
                payload,
            )
        )

        metadata.append(
            chunk_description(item)
        )

        if verbose:
            print(
                f"  [CREATE] "
                f"{item['manufacturer']} / "
                f"{item['model']}"
            )

    print()
    print("Device Types")
    print("-" * 72)

    print(
        f"Existing: {existing_count}"
    )

    print(
        f"Missing:  {len(pending)}"
    )

    if not apply_changes:
        return index

    if pending:
        payloads = [
            payload
            for _item, payload
            in pending
        ]

        print(
            f"Creating in batches of "
            f"{batch_size}..."
        )

        client.bulk_post(
            "/api/dcim/device-types/",
            payloads,
            batch_size,
            metadata,
        )

    # Refresh so every Device Type has a valid ID.
    return load_device_type_index(
        client
    )


# ===========================================================================
# Component indexes
# ===========================================================================

def load_component_index(
    client,
    endpoint,
):
    objects = client.get_all(
        endpoint
    )

    index = {}

    for obj in objects:
        device_type_id = related_id(
            obj.get("device_type")
        )

        name = obj.get("name")

        if (
            device_type_id
            and
            name
        ):
            index[
                (
                    device_type_id,
                    name,
                )
            ] = obj

    return index


# ===========================================================================
# Component payloads
# ===========================================================================

def build_component_payload(
    component_type,
    component,
    device_type_id,
    dependencies,
):
    spec = COMPONENT_SPECS[
        component_type
    ]

    payload = {
        "device_type": device_type_id,
    }

    for field in spec["fields"]:
        if field not in component:
            continue

        # Relationship fields require translation
        # from YAML names to NetBox IDs.

        if (
            component_type
            == "power-outlets"
            and
            field == "power_port"
        ):
            power_port_name = component[
                "power_port"
            ]

            power_port = (
                dependencies[
                    "power-ports"
                ].get(
                    (
                        device_type_id,
                        power_port_name,
                    )
                )
            )

            if not power_port:
                raise RuntimeError(
                    f"Unable to resolve power "
                    f"port '{power_port_name}' "
                    f"for Device Type ID "
                    f"{device_type_id}"
                )

            payload[
                "power_port"
            ] = power_port["id"]

            continue

        if (
            component_type
            == "front-ports"
            and
            field == "rear_port"
        ):
            rear_port_name = component[
                "rear_port"
            ]

            rear_port = (
                dependencies[
                    "rear-ports"
                ].get(
                    (
                        device_type_id,
                        rear_port_name,
                    )
                )
            )

            if not rear_port:
                raise RuntimeError(
                    f"Unable to resolve rear "
                    f"port '{rear_port_name}' "
                    f"for Device Type ID "
                    f"{device_type_id}"
                )

            payload[
                "rear_port"
            ] = rear_port["id"]

            continue

        payload[field] = component[field]

    return payload


# ===========================================================================
# Component provisioning
# ===========================================================================

def provision_component_type(
    client,
    component_type,
    items,
    device_types,
    dependencies,
    apply_changes,
    batch_size,
    verbose,
):
    spec = COMPONENT_SPECS[
        component_type
    ]

    existing = load_component_index(
        client,
        spec["endpoint"],
    )

    total_catalog = 0
    existing_count = 0

    pending_payloads = []
    pending_metadata = []

    for item in items:
        components = item[
            "data"
        ].get(
            component_type,
            [],
        )

        if not components:
            continue

        total_catalog += len(
            components
        )

        device_type = device_types.get(
            (
                item["manufacturer"],
                item["model"],
            )
        )

        # During dry-run, a missing Device Type has
        # no database ID. All its components are
        # necessarily missing as well.
        if not device_type:
            for component in components:
                if verbose:
                    print(
                        f"  [CREATE] "
                        f"{chunk_description(item, component)}"
                    )

            continue

        device_type_id = device_type["id"]

        for component in components:
            key = (
                device_type_id,
                component["name"],
            )

            if key in existing:
                existing_count += 1

                if verbose:
                    print(
                        f"  [EXISTS] "
                        f"{chunk_description(item, component)}"
                    )

                continue

            if verbose:
                print(
                    f"  [CREATE] "
                    f"{chunk_description(item, component)}"
                )

            if apply_changes:
                payload = (
                    build_component_payload(
                        component_type,
                        component,
                        device_type_id,
                        dependencies,
                    )
                )

                pending_payloads.append(
                    payload
                )

                pending_metadata.append(
                    chunk_description(
                        item,
                        component,
                    )
                )

    missing_count = (
        total_catalog
        - existing_count
    )

    print()
    print(spec["label"])
    print("-" * 72)

    print(
        f"Catalog:  {total_catalog}"
    )

    print(
        f"Existing: {existing_count}"
    )

    print(
        f"Missing:  {missing_count}"
    )

    if (
        apply_changes
        and
        pending_payloads
    ):
        print(
            f"Creating {len(pending_payloads)} "
            f"object(s) in batches of "
            f"{batch_size}..."
        )

        created = client.bulk_post(
            spec["endpoint"],
            pending_payloads,
            batch_size,
            pending_metadata,
        )

        for obj in created:
            device_type_id = related_id(
                obj.get("device_type")
            )

            name = obj.get("name")

            if (
                device_type_id
                and
                name
            ):
                existing[
                    (
                        device_type_id,
                        name,
                    )
                ] = obj

    return {
        "catalog": total_catalog,
        "existing": existing_count,
        "missing": missing_count,
        "objects": existing,
    }


# ===========================================================================
# Provision selected Device Types
# ===========================================================================

def provision(
    client,
    items,
    apply_changes,
    batch_size,
    verbose,
):
    # -----------------------------------------------------------------------
    # Manufacturers
    # -----------------------------------------------------------------------

    manufacturers = (
        provision_manufacturers(
            client,
            items,
            apply_changes,
            verbose,
        )
    )

    # In apply mode manufacturers have now been created.
    if apply_changes:
        manufacturers = {
            obj["name"]: obj
            for obj in client.get_all(
                "/api/dcim/manufacturers/"
            )
        }

    # -----------------------------------------------------------------------
    # Device Types
    # -----------------------------------------------------------------------

    platform_config = load_platform_config()
    # A single-type import only needs platforms for its selected manufacturers.
    selected_vendors = {item["manufacturer"] for item in items}
    platform_config["platforms"] = {
        slug: value for slug, value in platform_config["platforms"].items()
        if value["manufacturer"] in selected_vendors
    }
    platforms = provision_platforms(client, manufacturers, platform_config, apply_changes)
    print_platform_report(catalog_platform_report([item["data"] for item in items], platform_config))

    device_types = (
        provision_device_types(
            client,
            items,
            manufacturers,
            apply_changes,
            batch_size,
            verbose,
            platforms,
            platform_config,
        )
    )

    # -----------------------------------------------------------------------
    # Components
    # -----------------------------------------------------------------------

    enrich_device_types(client, items, device_types, platforms, platform_config, apply_changes)

    dependencies = {}

    summaries = {}

    for component_type in COMPONENT_ORDER:
        summary = (
            provision_component_type(
                client,
                component_type,
                items,
                device_types,
                dependencies,
                apply_changes,
                batch_size,
                verbose,
            )
        )

        summaries[
            component_type
        ] = summary

        # Needed by dependent component types.
        dependencies[
            component_type
        ] = summary["objects"]

    return summaries


# ===========================================================================
# Simple catalog summary
# ===========================================================================

def simple_summary(
    client,
    items,
):
    manufacturers = client.get_all(
        "/api/dcim/manufacturers/"
    )

    manufacturer_names = {
        obj["name"]
        for obj in manufacturers
    }

    required_manufacturers = {
        item["manufacturer"]
        for item in items
    }

    device_types = (
        load_device_type_index(
            client
        )
    )

    existing_dt = 0

    for item in items:
        if (
            item["manufacturer"],
            item["model"],
        ) in device_types:
            existing_dt += 1

    print()
    print("CATALOG SUMMARY")
    print("=" * 72)

    print(
        f"Device Types in catalog:    "
        f"{len(items)}"
    )

    print(
        f"Device Types existing:      "
        f"{existing_dt}"
    )

    print(
        f"Device Types missing:       "
        f"{len(items) - existing_dt}"
    )

    print()

    manufacturers_existing = len(
        required_manufacturers
        & manufacturer_names
    )

    manufacturers_missing = len(
        required_manufacturers
        - manufacturer_names
    )

    print(
        f"Manufacturers existing:     "
        f"{manufacturers_existing}"
    )

    print(
        f"Manufacturers missing:      "
        f"{manufacturers_missing}"
    )


# ===========================================================================
# Main
# ===========================================================================

def main():
    args = parse_args()

    if (
        args.batch_size < 1
        or
        args.batch_size > 1000
    ):
        sys.exit(
            "[ERROR] --batch-size must be "
            "between 1 and 1000"
        )

    if args.apply and not (
        args.all
        or
        args.device_type
    ):
        sys.exit(
            "[ERROR] --apply requires either "
            "--all or --device-type.\n\n"
            "Examples:\n"
            "  python3 scripts/"
            "provision_netbox.py "
            "--apply --device-type FG-60F\n"
            "  python3 scripts/"
            "provision_netbox.py "
            "--apply --all"
        )

    if not NETBOX_TOKEN:
        sys.exit(
            "[ERROR] NETBOX_TOKEN is not set"
        )

    if not CATALOG.exists():
        sys.exit(
            f"[ERROR] Catalog not found: "
            f"{CATALOG}"
        )

    # -----------------------------------------------------------------------
    # Connect
    # -----------------------------------------------------------------------

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

    netbox_version = status.get(
        "netbox-version",
        "unknown",
    )

    print("Atlas NetBox Provisioner")
    print("=" * 72)

    print(
        f"NetBox:      {netbox_version}"
    )

    print(
        f"Mode:        "
        f"{'APPLY' if args.apply else 'DRY-RUN'}"
    )

    print(
        f"Batch size:  {args.batch_size}"
    )

    if (
        netbox_version
        != TESTED_NETBOX_VERSION
    ):
        print()
        print(
            f"[WARNING] This provisioner was "
            f"developed against NetBox "
            f"{TESTED_NETBOX_VERSION}"
        )

    # -----------------------------------------------------------------------
    # Load catalog
    # -----------------------------------------------------------------------

    items, parse_errors = (
        load_catalog()
    )

    if parse_errors:
        print()
        print("Catalog parse errors:")

        for error in parse_errors:
            print(
                f"  [ERROR] {error}"
            )

        sys.exit(
            f"[ERROR] "
            f"{len(parse_errors)} "
            f"catalog file(s) failed"
        )

    validation_errors = (
        validate_catalog(items)
    )

    if validation_errors:
        print()
        print("Catalog validation errors:")

        for error in validation_errors:
            print(
                f"  [ERROR] {error}"
            )

        sys.exit(
            f"[ERROR] "
            f"{len(validation_errors)} "
            f"validation error(s)"
        )

    print(
        f"Catalog:     "
        f"{len(items)} Device Types"
    )

    print(
        "[OK] Catalog validation passed"
    )

    # -----------------------------------------------------------------------
    # No explicit target -> simple status summary
    # -----------------------------------------------------------------------

    selected = select_items(
        items,
        args,
    )

    if selected is None:
        simple_summary(
            client,
            items,
        )

        print()
        print(
            "Use --all for a complete "
            "component dry-run."
        )

        return

    print(
        f"Selected:    "
        f"{len(selected)} Device Type(s)"
    )

    if args.device_type:
        print(
            f"Target:      "
            f"{args.device_type}"
        )

    # -----------------------------------------------------------------------
    # Provision
    # -----------------------------------------------------------------------

    try:
        summaries = provision(
            client,
            selected,
            args.apply,
            args.batch_size,
            args.verbose,
        )

    except Exception as exc:
        print()
        sys.exit(
            f"[ERROR] Provisioning stopped: "
            f"{exc}"
        )

    # -----------------------------------------------------------------------
    # Final summary
    # -----------------------------------------------------------------------

    print()
    print("=" * 72)

    if args.apply:
        print("APPLY COMPLETE")
    else:
        print("DRY-RUN COMPLETE")

    print("=" * 72)

    total_components = 0
    total_existing = 0
    total_missing = 0

    for component_type in COMPONENT_ORDER:
        summary = summaries[
            component_type
        ]

        if not summary["catalog"]:
            continue

        print(
            f"{COMPONENT_SPECS[component_type]['label']:<24}"
            f" catalog={summary['catalog']:<7}"
            f" existing={summary['existing']:<7}"
            f" missing={summary['missing']}"
        )

        total_components += (
            summary["catalog"]
        )

        total_existing += (
            summary["existing"]
        )

        total_missing += (
            summary["missing"]
        )

    print("-" * 72)

    print(
        f"{'TOTAL COMPONENTS':<24}"
        f" catalog={total_components:<7}"
        f" existing={total_existing:<7}"
        f" missing={total_missing}"
    )

    print()

    if args.apply:
        print(
            "[OK] Provisioning run completed."
        )

        print(
            "Run the same command again "
            "without --apply to verify "
            "idempotency."
        )

    else:
        print(
            "No changes were made to NetBox."
        )


if __name__ == "__main__":
    main()
