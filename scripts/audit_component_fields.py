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
    "interfaces",
    "rear-ports",
    "front-ports",
    "module-bays",
    "device-bays",
    "inventory-items",
    "cooling-intakes",
    "cooling-outflows",
]


def main():
    files = sorted(
        CATALOG.glob("*/*.yaml")
    )

    fields = defaultdict(Counter)
    examples = defaultdict(
        lambda: defaultdict(list)
    )

    errors = []

    for yaml_file in files:
        try:
            with yaml_file.open(
                "r",
                encoding="utf-8",
            ) as handle:
                data = yaml.safe_load(
                    handle
                )

            if not isinstance(data, dict):
                raise ValueError(
                    "YAML root is not an object"
                )

        except Exception as exc:
            errors.append(
                f"{yaml_file}: {exc}"
            )
            continue

        for component_type in COMPONENT_TYPES:
            components = data.get(
                component_type,
                [],
            )

            if not components:
                continue

            for component in components:
                if not isinstance(
                    component,
                    dict,
                ):
                    continue

                for field in component.keys():
                    fields[
                        component_type
                    ][field] += 1

                    if len(
                        examples[
                            component_type
                        ][field]
                    ) < 3:

                        examples[
                            component_type
                        ][field].append(
                            (
                                yaml_file.name,
                                component.get(
                                    "name",
                                    "<unnamed>",
                                ),
                            )
                        )

    print(
        "Atlas Component Field Audit"
    )
    print("=" * 80)

    for component_type in COMPONENT_TYPES:

        if not fields[component_type]:
            continue

        print()
        print(component_type)
        print("-" * 80)

        print(
            f"{'Field':<30}"
            f"{'Occurrences':>12}"
        )

        print(
            f"{'-' * 30}"
            f"{'-' * 12}"
        )

        for field, count in sorted(
            fields[
                component_type
            ].items()
        ):

            print(
                f"{field:<30}"
                f"{count:>12}"
            )

            for (
                filename,
                component_name,
            ) in examples[
                component_type
            ][field]:

                print(
                    f"    example: "
                    f"{filename} / "
                    f"{component_name}"
                )

    print()

    if errors:
        print("Errors")
        print("-" * 80)

        for error in errors:
            print(
                f"[ERROR] {error}"
            )

        raise SystemExit(
            f"[ERROR] "
            f"{len(errors)} file(s) failed"
        )

    print(
        "[OK] Field audit completed"
    )


if __name__ == "__main__":
    main()
