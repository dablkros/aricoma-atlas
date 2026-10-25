import tempfile
import unittest
from pathlib import Path

from atlas.inventory.device_types import (
    DeviceTypeCandidate,
    find_netbox_candidates,
    find_yaml_candidates,
    normalized_identifier,
    recommended_action,
)


class FakeNetBoxClient:
    def __init__(self, items):
        self.items = items
        self.endpoints = []

    def get_all(self, endpoint):
        self.endpoints.append(endpoint)
        return self.items


def candidate(source):
    return DeviceTypeCandidate(
        source=source,
        manufacturer="Cisco",
        model="CBS350-16T-2G",
        slug="cbs350-16t-2g",
    )


class DeviceTypeDiscoveryTests(unittest.TestCase):
    def test_normalization_is_exact_but_case_and_whitespace_insensitive(self):
        self.assertEqual(
            normalized_identifier("  CBS350-16T-2G  "),
            normalized_identifier("cbs350-16t-2g"),
        )
        self.assertNotEqual(
            normalized_identifier("CBS350"),
            normalized_identifier("CBS350-16T-2G"),
        )

    def test_netbox_search_matches_model_or_slug(self):
        client = FakeNetBoxClient([
            {
                "id": 42,
                "model": "CBS350-16T-2G",
                "slug": "cisco-cbs350-16t-2g",
                "manufacturer": {"name": "Cisco"},
            },
            {
                "id": 43,
                "model": "Different model",
                "slug": "different-model",
                "manufacturer": {"name": "Cisco"},
            },
        ])

        result = find_netbox_candidates(client, "cisco-cbs350-16t-2g")

        self.assertEqual(["/api/dcim/device-types/"], client.endpoints)
        self.assertEqual(1, len(result))
        self.assertEqual(42, result[0].object_id)

    def test_yaml_search_matches_filename_and_reports_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "Cisco"
            vendor.mkdir()
            (vendor / "CBS350-16T-2G.yaml").write_text(
                "manufacturer: Cisco\nmodel: Cisco CBS 350\nslug: cisco-cbs-350\n",
                encoding="utf-8",
            )
            (vendor / "invalid.yaml").write_text("model: [\n", encoding="utf-8")

            matches, errors = find_yaml_candidates(
                root,
                "CBS350-16T-2G",
                source="ATLAS_CATALOG",
            )

        self.assertEqual(1, len(matches))
        self.assertEqual("ATLAS_CATALOG", matches[0].source)
        self.assertEqual(1, len(errors))
        self.assertIn("invalid.yaml", errors[0])

    def test_recommended_actions_cover_safe_flow(self):
        netbox = candidate("NETBOX")
        catalog = candidate("ATLAS_CATALOG")
        upstream = candidate("PINNED_UPSTREAM")

        cases = (
            (([], [], []), "NOT_FOUND"),
            (([netbox], [], []), "ALREADY_IN_NETBOX"),
            (([], [catalog], []), "ADD_FROM_ATLAS_CATALOG"),
            (([netbox], [catalog], []), "RECONCILE"),
            (([], [], [upstream]), "CATALOG_REVIEW_REQUIRED"),
            (([], [catalog, catalog], []), "CONFLICT"),
        )
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, recommended_action(*arguments))


if __name__ == "__main__":
    unittest.main()
