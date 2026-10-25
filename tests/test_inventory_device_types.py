import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas.inventory.config import normalize_inventory_config
from atlas.inventory.device_types import (
    DeviceTypeCandidate,
    find_netbox_candidates,
    find_yaml_candidates,
    find_yaml_candidates_for_queries,
    normalized_identifier,
    recommended_action,
)
from atlas.inventory.models import InventoryRow
from scripts import import_netbox_inventory as inventory_cli


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

    def test_multiple_yaml_queries_are_resolved_in_one_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "Cisco"
            vendor.mkdir()
            (vendor / "MODEL-A.yaml").write_text(
                "manufacturer: Cisco\nmodel: Model A\nslug: model-a\n",
                encoding="utf-8",
            )
            (vendor / "MODEL-B.yaml").write_text(
                "manufacturer: Cisco\nmodel: Model B\nslug: model-b\n",
                encoding="utf-8",
            )

            matches, errors = find_yaml_candidates_for_queries(
                root,
                ["MODEL-A", "model-b"],
                source="PINNED_UPSTREAM",
            )

        self.assertEqual([], errors)
        self.assertEqual("Model A", matches["model-a"][0].model)
        self.assertEqual("Model B", matches["model-b"][0].model)

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

        local = DeviceTypeCandidate(
            source="ATLAS_LOCAL",
            manufacturer="Cisco",
            model="CBS350-16T-2G",
            slug="cbs350-16t-2g",
        )
        self.assertEqual(
            "ADD_FROM_ATLAS_LOCAL",
            recommended_action([], [], [], [local]),
        )
        self.assertEqual(
            "CONFLICT",
            recommended_action([], [], [upstream], [local]),
        )


class InventoryDeviceTypePreflightTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeNetBoxClient([])
        self.client.get_all = self.get_all
        self.config = normalize_inventory_config({})
        self.row = InventoryRow(
            number=2,
            hostname="SW01",
            model="CBS350-16T-2G",
            management_ip="192.0.2.10",
            site="lab",
        )
        self.item = {
            "file": Path("/catalog/Cisco/CBS350-16T-2G.yaml"),
            "filename": "CBS350-16T-2G",
            "manufacturer": "Cisco",
            "model": "CBS350-16T-2G",
            "slug": "cbs350-16t-2g",
            "data": {
                "manufacturer": "Cisco",
                "model": "CBS350-16T-2G",
                "slug": "cbs350-16t-2g",
            },
        }

    def get_all(self, endpoint, params=None):
        if endpoint == "/api/dcim/device-types/":
            return []
        if endpoint == "/api/dcim/platforms/":
            return [{"id": 30, "slug": "cisco-cbs"}]
        raise AssertionError(endpoint)

    @patch.object(inventory_cli.provision_netbox, "provision")
    @patch.object(inventory_cli.provision_netbox, "validate_item", return_value=[])
    @patch.object(inventory_cli.provision_netbox, "load_catalog")
    @patch.object(inventory_cli.bootstrap_netbox, "catalog_status", return_value=(True, []))
    @patch.object(inventory_cli, "default_platform", return_value="cisco-cbs")
    @patch.object(inventory_cli, "load_platform_config", return_value={})
    def test_catalog_type_is_planned_before_csv_dry_run(
        self,
        _load_platform,
        _default_platform,
        _catalog_status,
        load_catalog,
        _validate,
        provision,
    ):
        load_catalog.return_value = ([self.item], [])

        planned, errors = inventory_cli.prepare_import_device_types(
            self.client,
            [self.row],
            self.config,
            apply=False,
        )

        self.assertEqual({}, errors)
        self.assertEqual("CBS350-16T-2G", planned[0]["model"])
        self.assertEqual(-1, planned[0]["id"])
        provision.assert_called_once()
        self.assertFalse(provision.call_args.args[2])

    @patch.object(inventory_cli.provision_netbox, "provision")
    @patch.object(inventory_cli.provision_netbox, "validate_item", return_value=[])
    @patch.object(inventory_cli.provision_netbox, "load_catalog")
    @patch.object(inventory_cli.bootstrap_netbox, "catalog_status", return_value=(True, []))
    @patch.object(inventory_cli, "default_platform", return_value="cisco-cbs")
    @patch.object(inventory_cli, "load_platform_config", return_value={})
    def test_apply_provisions_type_without_synthetic_object(
        self,
        _load_platform,
        _default_platform,
        _catalog_status,
        load_catalog,
        _validate,
        provision,
    ):
        load_catalog.return_value = ([self.item], [])

        planned, errors = inventory_cli.prepare_import_device_types(
            self.client,
            [self.row],
            self.config,
            apply=True,
        )

        self.assertEqual(([], {}), (planned, errors))
        provision.assert_called_once()
        self.assertTrue(provision.call_args.args[2])

    @patch.object(inventory_cli.provision_netbox, "provision")
    @patch.object(inventory_cli.provision_netbox, "validate_item", return_value=[])
    @patch.object(inventory_cli.provision_netbox, "load_catalog", return_value=([], []))
    @patch.object(inventory_cli.bootstrap_netbox, "catalog_status", return_value=(True, []))
    @patch.object(inventory_cli.bootstrap_netbox, "upstream_git_output", return_value="abc123")
    @patch.object(
        inventory_cli.bootstrap_netbox,
        "load_manifest",
        return_value={"upstream": {"commit": "abc123"}},
    )
    @patch.object(inventory_cli, "default_platform", return_value="cisco-cbs")
    @patch.object(inventory_cli, "load_platform_config", return_value={})
    def test_missing_catalog_type_falls_back_to_pinned_upstream(
        self,
        _load_platform,
        _default_platform,
        _manifest,
        _git_commit,
        _catalog_status,
        _load_catalog,
        _validate,
        provision,
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CBS350-16T-2G.yaml"
            path.write_text(
                "manufacturer: Cisco\n"
                "model: CBS350-16T-2G\n"
                "slug: cbs350-16t-2g\n",
                encoding="utf-8",
            )
            upstream = DeviceTypeCandidate(
                source="PINNED_UPSTREAM",
                manufacturer="Cisco",
                model="CBS350-16T-2G",
                slug="cbs350-16t-2g",
                path=str(path),
            )
            with patch.object(
                inventory_cli,
                "find_yaml_candidates_for_queries",
                return_value=({"cbs350-16t-2g": [upstream]}, []),
            ):
                planned, errors = inventory_cli.prepare_import_device_types(
                    self.client,
                    [self.row],
                    self.config,
                    apply=False,
                )

        self.assertEqual({}, errors)
        self.assertEqual("PINNED_UPSTREAM", planned[0]["atlas_source"])
        provision.assert_called_once()

    @patch.object(inventory_cli.provision_netbox, "provision")
    @patch.object(inventory_cli.provision_netbox, "validate_item", return_value=[])
    @patch.object(inventory_cli.provision_netbox, "load_catalog", return_value=([], []))
    @patch.object(inventory_cli.bootstrap_netbox, "catalog_status", return_value=(True, []))
    @patch.object(inventory_cli.bootstrap_netbox, "upstream_git_output", return_value="abc123")
    @patch.object(
        inventory_cli.bootstrap_netbox,
        "load_manifest",
        return_value={"upstream": {"commit": "abc123"}},
    )
    @patch.object(inventory_cli, "default_platform", return_value="cisco-cbs")
    @patch.object(inventory_cli, "load_platform_config", return_value={})
    def test_manual_runtime_yaml_is_used_after_upstream_miss(
        self,
        _load_platform,
        _default_platform,
        _manifest,
        _git_commit,
        _catalog_status,
        _load_catalog,
        _validate,
        provision,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream_root = root / "upstream"
            (upstream_root / "device-types").mkdir(parents=True)
            local_root = root / "local"
            local_root.mkdir()
            (local_root / "LOCAL-ONLY-1000.yaml").write_text(
                "manufacturer: Cisco\n"
                "model: LOCAL-ONLY-1000\n"
                "slug: local-only-1000\n",
                encoding="utf-8",
            )
            local_row = InventoryRow(
                number=2,
                hostname="SW01",
                model="LOCAL-ONLY-1000",
                management_ip="192.0.2.10",
                site="lab",
            )
            with (
                patch.object(inventory_cli, "LOCAL_DEVICE_TYPE_DIR", local_root),
                patch.object(
                    inventory_cli.bootstrap_netbox,
                    "UPSTREAM_DIR",
                    upstream_root,
                ),
            ):
                planned, errors = inventory_cli.prepare_import_device_types(
                    self.client,
                    [local_row],
                    self.config,
                    apply=False,
                )

        self.assertEqual({}, errors)
        self.assertEqual("ATLAS_LOCAL", planned[0]["atlas_source"])
        provision.assert_called_once()

    @patch.object(inventory_cli.provision_netbox, "provision")
    @patch.object(inventory_cli.provision_netbox, "validate_item", return_value=[])
    @patch.object(inventory_cli.provision_netbox, "load_catalog", return_value=([], []))
    @patch.object(inventory_cli.bootstrap_netbox, "catalog_status", return_value=(True, []))
    @patch.object(inventory_cli.bootstrap_netbox, "upstream_git_output", return_value="abc123")
    @patch.object(
        inventory_cli.bootstrap_netbox,
        "load_manifest",
        return_value={"upstream": {"commit": "abc123"}},
    )
    @patch.object(inventory_cli, "default_platform", return_value="cisco-cbs")
    @patch.object(inventory_cli, "load_platform_config", return_value={})
    def test_pinned_upstream_takes_priority_over_manual_runtime_yaml(
        self,
        _load_platform,
        _default_platform,
        _manifest,
        _git_commit,
        _catalog_status,
        _load_catalog,
        _validate,
        provision,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_path = root / "local" / "CBS350-16T-2G.yaml"
            local_path.parent.mkdir()
            local_path.write_text(
                "manufacturer: Local override\n"
                "model: CBS350-16T-2G\n"
                "slug: local-cbs350-16t-2g\n",
                encoding="utf-8",
            )
            upstream_path = root / "upstream" / "CBS350-16T-2G.yaml"
            upstream_path.parent.mkdir()
            upstream_path.write_text(
                "manufacturer: Cisco\n"
                "model: CBS350-16T-2G\n"
                "slug: cbs350-16t-2g\n",
                encoding="utf-8",
            )
            upstream = DeviceTypeCandidate(
                source="PINNED_UPSTREAM",
                manufacturer="Cisco",
                model="CBS350-16T-2G",
                slug="cbs350-16t-2g",
                path=str(upstream_path),
            )
            with (
                patch.object(inventory_cli, "LOCAL_DEVICE_TYPE_DIR", local_path.parent),
                patch.object(
                    inventory_cli,
                    "find_yaml_candidates_for_queries",
                    return_value=({"cbs350-16t-2g": [upstream]}, []),
                ),
            ):
                planned, errors = inventory_cli.prepare_import_device_types(
                    self.client,
                    [self.row],
                    self.config,
                    apply=False,
                )

        self.assertEqual({}, errors)
        self.assertEqual("PINNED_UPSTREAM", planned[0]["atlas_source"])
        self.assertEqual("Cisco", planned[0]["manufacturer"]["name"])
        provision.assert_called_once()

if __name__ == "__main__":
    unittest.main()
