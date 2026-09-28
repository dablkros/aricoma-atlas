#!/usr/bin/env python3

from collections import Counter, defaultdict
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "build" / "device-types"


COMPONENT_TYPES = [
    "console-ports",
    "console-server-ports",
    "power-ports",
    "power-outlets",
    "cooling-intakes",
    "cooling-outflows",
    "interfaces",
    "rear-ports",
    "front-ports",
    "module-bays",
    "device-bays",
    "inventory-items",
]


EXPECTED_TOP_LEVEL_KEYS = {
    "manufacturer",
    "model",
    "slug",
    "part_number",
    "u_height",
    "is_full_depth",
    "airflow",
    "cooling_method",
    "subdevice_role",
    "weight",
    "weight_unit",
    "comments",
    "front_image",
    "rear_image",
    "tags",
    *COMPONENT_TYPES,
}


def main():
    if not CATALOG.exists():
        raise SystemExit(
            f"[ERROR] Catalog not found: {CATALOG}"
        )

    files = sorted(
        CATALOG.glob("*/*.yaml")
    )

    component_counts = Counter()
    device_counts = Counter()
    vendor_counts = Counter()

    examples = defaultdict(list)
    unknown_top_level = defaultdict(list)

    module_bay_type_refs = Counter()
    module_bay_type_devices = set()

    front_port_references = 0
    power_outlet_references = 0

    errors = []

    for yaml_file in files:
        try:
            with yaml_file.open(
                "r",
                encoding="utf-8",
            ) as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                raise ValueError(
                    "YAML root is not an object"
                )

        except Exception as exc:
            errors.append(
                (
                    str(yaml_file),
                    str(exc),
                )
            )
            continue

        vendor = yaml_file.parent.name

        vendor_counts[vendor] += 1

        # -----------------------------------------------------
        # Unknown top-level keys
        # -----------------------------------------------------

        for key in data.keys():
            if key not in EXPECTED_TOP_LEVEL_KEYS:
                unknown_top_level[key].append(
                    f"{vendor}/{yaml_file.name}"
                )

        # -----------------------------------------------------
        # Component counts
        # -----------------------------------------------------

        for component_type in COMPONENT_TYPES:

            components = data.get(
                component_type,
                [],
            )

            if not components:
                continue

            if not isinstance(components, list):
                errors.append(
                    (
                        str(yaml_file),
                        f"{component_type} is not a list",
                    )
                )
                continue

            device_counts[
                component_type
            ] += 1

            component_counts[
                component_type
            ] += len(components)

            if len(
                examples[component_type]
            ) < 5:

                examples[
                    component_type
                ].append(
                    yaml_file.name
                )

        # -----------------------------------------------------
        # Power outlet -> power port references
        # -----------------------------------------------------

        for power_outlet in data.get(
            "power-outlets",
            [],
        ):

            if power_outlet.get(
                "power_port"
            ):
                power_outlet_references += 1

        # -----------------------------------------------------
        # Front -> rear references
        # -----------------------------------------------------

        for front_port in data.get(
            "front-ports",
            [],
        ):

            if front_port.get(
                "rear_port"
            ):
                front_port_references += 1

        # -----------------------------------------------------
        # Module Bay Types (NetBox 4.7+)
        # -----------------------------------------------------

        for module_bay in data.get(
            "module-bays",
            [],
        ):

            bay_types = (
                module_bay.get(
                    "module_bay_types"
                )
                or
                module_bay.get(
                    "module-bay-types"
                )
                or
                []
            )

            if isinstance(bay_types, str):
                bay_types = [
                    bay_types
                ]

            if bay_types:
                module_bay_type_devices.add(
                    f"{vendor}/{yaml_file.name}"
                )

            for bay_type in bay_types:
                module_bay_type_refs[
                    str(bay_type)
                ] += 1

    # ---------------------------------------------------------
    # Output
    # ---------------------------------------------------------

    print("Atlas Device Catalog Audit")
    print("=" * 78)

    print(
        f"Device Type files: "
        f"{len(files)}"
    )

    print()
    print("Vendors")
    print("-" * 78)

    for vendor, count in sorted(
        vendor_counts.items()
    ):

        print(
            f"{vendor:<22}"
            f"{count:>8}"
        )

    # ---------------------------------------------------------

    print()
    print("Component coverage")
    print("-" * 78)

    print(
        f"{'Component':<26}"
        f"{'Devices':>10}"
        f"{'Templates':>14}"
    )

    print(
        f"{'-' * 26}"
        f"{'-' * 10}"
        f"{'-' * 14}"
    )

    for component_type in COMPONENT_TYPES:

        print(
            f"{component_type:<26}"
            f"{device_counts[component_type]:>10}"
            f"{component_counts[component_type]:>14}"
        )

    # ---------------------------------------------------------

    print()
    print("Examples")
    print("-" * 78)

    for component_type in COMPONENT_TYPES:

        if not examples[
            component_type
        ]:
            continue

        print(
            f"{component_type}:"
        )

        for example in examples[
            component_type
        ]:

            print(
                f"  - {example}"
            )

    # ---------------------------------------------------------

    print()
    print("Dependencies")
    print("-" * 78)

    print(
        "Front ports referencing rear ports:   "
        f"{front_port_references}"
    )

    print(
        "Power outlets referencing power ports:"
        f" {power_outlet_references}"
    )

    # ---------------------------------------------------------

    print()
    print("Module Bay Types (NetBox 4.7+)")
    print("-" * 78)

    print(
        "Devices using module_bay_types: "
        f"{len(module_bay_type_devices)}"
    )

    print(
        "Unique referenced bay types:    "
        f"{len(module_bay_type_refs)}"
    )

    if module_bay_type_refs:

        print()
        print(
            f"{'Bay type':<45}"
            f"{'References':>12}"
        )

        print(
            f"{'-' * 45}"
            f"{'-' * 12}"
        )

        for name, count in sorted(
            module_bay_type_refs.items()
        ):

            print(
                f"{name:<45}"
                f"{count:>12}"
            )

    # ---------------------------------------------------------

    print()
    print("Unknown top-level YAML keys")
    print("-" * 78)

    if not unknown_top_level:

        print(
            "[OK] No unknown top-level keys"
        )

    else:

        for key, filenames in sorted(
            unknown_top_level.items()
        ):

            print(
                f"[WARNING] {key}: "
                f"{len(filenames)} file(s)"
            )

            for filename in filenames[:5]:

                print(
                    f"          - {filename}"
                )

            if len(filenames) > 5:

                print(
                    f"          ... +"
                    f"{len(filenames) - 5} more"
                )

    # ---------------------------------------------------------

    print()

    if errors:

        print("Errors")
        print("-" * 78)

        for filename, error in errors:

            print(
                f"[ERROR] "
                f"{filename}: "
                f"{error}"
            )

        raise SystemExit(
            f"\n[ERROR] "
            f"{len(errors)} YAML files failed"
        )

    print(
        "[OK] All catalog files parsed successfully"
    )


if __name__ == "__main__":
    main()
