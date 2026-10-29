import copy
import ipaddress
import unittest

from atlas.inventory.config import DEFAULT_ROLE_MAPPINGS, normalize_inventory_config
from atlas.inventory.importer import InventoryConflict, InventoryImporter
from atlas.inventory.models import ImportStatus, InventoryRow


class FakeNetBoxClient:
    def __init__(self):
        self.objects = {
            "/api/tenancy/tenants/": [],
            "/api/tenancy/tenant-groups/": [],
            "/api/dcim/sites/": [],
            "/api/dcim/device-types/": [],
            "/api/dcim/platforms/": [],
            "/api/dcim/device-roles/": [],
            "/api/ipam/vrfs/": [],
            "/api/ipam/prefixes/": [],
            "/api/dcim/devices/": [],
            "/api/dcim/interfaces/": [],
            "/api/ipam/ip-addresses/": [],
        }
        self.writes = []
        self.fail_endpoint = None
        self.next_id = 100

    @staticmethod
    def _related(value):
        return value.get("id") if isinstance(value, dict) else value

    def get_all(self, endpoint, params=None):
        params = params or {}
        values = self.objects[endpoint]
        result = []
        for item in values:
            matches = True
            for key, expected in params.items():
                field = key
                if key == "device_id":
                    field = "device"
                actual = self._related(item.get(field))
                if endpoint == "/api/ipam/ip-addresses/" and key == "address":
                    actual = str(ipaddress.ip_interface(item["address"]).ip)
                if actual != expected:
                    matches = False
                    break
            if matches:
                result.append(copy.deepcopy(item))
        return result

    def post(self, endpoint, payload):
        self.writes.append(("POST", endpoint, copy.deepcopy(payload)))
        if endpoint == self.fail_endpoint:
            raise RuntimeError("simulated_api_failure")
        item = {"id": self.next_id, **copy.deepcopy(payload)}
        self.next_id += 1
        self.objects[endpoint].append(item)
        return copy.deepcopy(item)

    def patch(self, endpoint, payload):
        self.writes.append(("PATCH", endpoint, copy.deepcopy(payload)))
        collection, identifier = endpoint.rsplit("/", 2)[:2]
        collection += "/"
        identifier = int(identifier)
        for item in self.objects[collection]:
            if item["id"] == identifier:
                item.update(copy.deepcopy(payload))
                return copy.deepcopy(item)
        raise RuntimeError("patch_target_not_found")


class InventoryImporterTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeNetBoxClient()
        self.tenant = {"id": 1, "name": "ACME Slovakia", "slug": "acme-slovakia"}
        self.site_br = {
            "id": 10, "name": "Bratislava DC", "slug": "bratislava-dc",
            "tenant": {"id": 1},
        }
        self.site_za = {
            "id": 11, "name": "Zilina Office", "slug": "zilina-office",
            "tenant": {"id": 1},
        }
        self.platform = {"id": 30, "name": "Cisco IOS", "slug": "cisco-ios"}
        self.device_type = {
            "id": 20, "model": "C9500-48Y4C", "default_platform": self.platform,
        }
        self.role = {"id": 40, "name": "Network Device", "slug": "network-device"}
        self.client.objects["/api/tenancy/tenants/"].append(self.tenant)
        self.client.objects["/api/dcim/sites/"].extend([self.site_br, self.site_za])
        self.client.objects["/api/dcim/platforms/"].append(self.platform)
        self.client.objects["/api/dcim/device-types/"].append(self.device_type)
        self.client.objects["/api/dcim/device-roles/"].append(self.role)
        self.config = normalize_inventory_config({
            "default_tenant": {
                "name": "ACME Slovakia", "slug": "acme-slovakia", "provision": True,
            }
        })
        self.importer = InventoryImporter(self.client, self.config)

    def row(self, number=2, hostname="BR-SW01", site="bratislava-dc", **changes):
        values = dict(
            number=number,
            hostname=hostname,
            model="C9500-48Y4C",
            management_ip="10.0.0.10",
            tenant=None,
            site=site,
            device_role=None,
            platform=None,
            vrf=None,
        )
        values.update(changes)
        return InventoryRow(**values)

    def test_01_create_tenant(self):
        self.client.objects["/api/tenancy/tenants/"].clear()
        tenant, operation = self.importer.ensure_tenant("New Customer", apply=True)
        self.assertEqual(operation, "CREATE")
        self.assertEqual(tenant["slug"], "new-customer")

    def test_02_reuse_existing_tenant_without_write(self):
        tenant, operation = self.importer.ensure_tenant("ACME Slovakia", apply=True)
        self.assertEqual((tenant["id"], operation), (1, "UNCHANGED"))
        self.assertEqual(self.client.writes, [])

    def test_optional_tenant_group_is_created_idempotently(self):
        self.client.objects["/api/tenancy/tenants/"].clear()
        tenant, operation = self.importer.ensure_tenant(
            "Customer A", group="Customers", apply=True
        )
        repeated, repeated_operation = self.importer.ensure_tenant(
            "Customer A", group="Customers", apply=True
        )
        self.assertEqual(operation, "CREATE")
        self.assertEqual(repeated_operation, "UNCHANGED")
        self.assertEqual(tenant["id"], repeated["id"])
        self.assertEqual(len(self.client.objects["/api/tenancy/tenant-groups/"]), 1)

    def test_03_create_multiple_sites_under_one_tenant(self):
        self.client.objects["/api/dcim/sites/"].clear()
        first, _ = self.importer.ensure_site(self.tenant, "Bratislava DC", apply=True)
        second, _ = self.importer.ensure_site(self.tenant, "Kosice Branch", apply=True)
        self.assertEqual(first["tenant"], 1)
        self.assertEqual(second["tenant"], 1)
        self.assertEqual(first["slug"], "acme-slovakia-bratislava-dc")
        self.assertEqual(second["slug"], "acme-slovakia-kosice-branch")

    def test_vrf_bootstrap_is_idempotent_and_tenant_owned(self):
        first, operation = self.importer.ensure_vrf(
            self.tenant, "acme-mgmt", apply=True
        )
        second, second_operation = self.importer.ensure_vrf(
            self.tenant, "acme-mgmt", apply=True
        )
        self.assertEqual(operation, "CREATE")
        self.assertEqual(second_operation, "UNCHANGED")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["tenant"], self.tenant["id"])
        self.assertTrue(first["enforce_unique"])

    def test_04_same_site_name_for_another_tenant_gets_unique_slug(self):
        self.site_br["tenant"] = {"id": 999}
        site, operation = self.importer.ensure_site(
            self.tenant, "Bratislava DC", apply=True
        )
        self.assertEqual(operation, "CREATE")
        self.assertEqual(site["tenant"], self.tenant["id"])
        self.assertEqual(site["slug"], "acme-slovakia-bratislava-dc")

    def test_tenant_slug_identity_conflict_is_not_overwritten(self):
        with self.assertRaisesRegex(InventoryConflict, "identity_conflict"):
            self.importer.ensure_tenant("Different Customer", apply=True, slug="acme-slovakia")

    def test_site_name_with_different_slug_is_not_duplicated(self):
        self.site_br["slug"] = "legacy-bratislava"
        site, operation = self.importer.ensure_site(
            self.tenant, "Bratislava DC", apply=True
        )
        self.assertEqual((site["id"], operation), (10, "UNCHANGED"))
        self.assertEqual(self.client.writes, [])

    def test_05_imports_multiple_sites(self):
        report = self.importer.import_rows([
            self.row(hostname="BR-SW01", site="bratislava-dc"),
            self.row(number=3, hostname="ZA-SW01", site="zilina-office", management_ip="10.0.1.10"),
        ])
        self.assertEqual([row.status for row in report.rows], [ImportStatus.CREATE] * 2)

    def test_06_cli_site_is_default(self):
        report = self.importer.import_rows([self.row(site=None)], site_override="bratislava-dc")
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_configured_default_site_is_used_when_row_site_is_empty(self):
        config = normalize_inventory_config({
            "default_tenant": {
                "name": "ACME Slovakia", "slug": "acme-slovakia", "provision": True,
            },
            "default_site": {
                "name": "Bratislava DC", "slug": "bratislava-dc", "provision": True,
            },
        })
        importer = InventoryImporter(self.client, config)

        report = importer.import_rows([self.row(site=None)])

        self.assertEqual(report.rows[0].site, "bratislava-dc")
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_default_site_requires_default_tenant(self):
        with self.assertRaisesRegex(ValueError, "requires default_tenant"):
            normalize_inventory_config({
                "default_site": {
                    "name": "Bratislava DC",
                    "slug": "bratislava-dc",
                    "provision": True,
                }
            })

    def test_default_role_mappings_are_present_and_operator_overrides_win(self):
        config = normalize_inventory_config({
            "role_mappings": {
                "models": {"SN2410": "router"},
            },
        })

        self.assertEqual(config["default_device_role"], "network-device")
        self.assertEqual(config["role_mappings"]["models"]["SN2410"], "router")
        self.assertEqual(
            config["role_mappings"]["models"]["Nexus 93360YC-FX2"],
            DEFAULT_ROLE_MAPPINGS["models"]["Nexus 93360YC-FX2"],
        )

    def test_upstream_marketing_model_resolves_to_baseline_switch_role(self):
        self.device_type.update({
            "model": "Catalyst 9500-48Y4C",
            "part_number": "C9500-48Y4C",
        })
        switch_role = {"id": 41, "name": "Switch", "slug": "switch"}
        self.client.objects["/api/dcim/device-roles/"].append(switch_role)

        report = self.importer.import_rows([self.row()], apply=True)

        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)
        created_device = next(
            payload
            for method, endpoint, payload in self.client.writes
            if method == "POST" and endpoint == "/api/dcim/devices/"
        )
        self.assertEqual(created_device["role"], switch_role["id"])

    def test_manufacturer_prefixed_csv_model_resolves_existing_device_type(self):
        self.device_type.update({
            "manufacturer": {"id": 50, "name": "F5"},
            "model": "BIG-IP r4800",
            "slug": "f5-big-ip-r4800",
            "part_number": "r4800",
            "default_platform": None,
        })

        report = self.importer.import_rows([
            self.row(model="F5 BIG-IP r4800"),
        ])

        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_07_row_site_is_used(self):
        report = self.importer.import_rows([self.row(site="zilina-office")])
        self.assertEqual(report.rows[0].site, "zilina-office")
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_08_row_site_conflicting_with_cli_is_reported(self):
        report = self.importer.import_rows(
            [self.row(site="zilina-office")], site_override="bratislava-dc"
        )
        self.assertEqual(report.rows[0].status, ImportStatus.CONFLICT)

    def test_09_missing_or_unknown_site_is_invalid(self):
        missing = self.importer.import_rows([self.row(site=None)])
        unknown = self.importer.import_rows([self.row(site="unknown")])
        self.assertEqual(missing.rows[0].reason, "site_not_selected")
        self.assertEqual(unknown.rows[0].reason, "site_not_found")

    def test_10_existing_device_type_is_resolved(self):
        report = self.importer.import_rows([self.row()])
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_device_type_lookup_normalizes_case_and_whitespace(self):
        report = self.importer.import_rows([self.row(model="  c9500-48y4c  ")])
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_11_unknown_device_type_is_invalid(self):
        report = self.importer.import_rows([self.row(model="UNKNOWN")])
        self.assertEqual(report.rows[0].reason, "device_type_not_found")

    def test_planned_device_type_allows_integrated_dry_run(self):
        planned = {
            "id": -1,
            "model": "CBS350-16T-2G",
            "default_platform": self.platform,
        }
        importer = InventoryImporter(
            self.client,
            self.config,
            planned_device_types=[planned],
        )

        report = importer.import_rows([
            self.row(model="CBS350-16T-2G"),
        ])

        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)
        self.assertEqual(
            report.rows[0].operation,
            "CREATE_DEVICE_TYPE_DEVICE_AND_MANAGEMENT",
        )
        self.assertEqual(self.client.writes, [])

    def test_device_type_preflight_error_is_reported_per_row(self):
        importer = InventoryImporter(
            self.client,
            self.config,
            device_type_errors={"unknown": "device_type_not_found_in_pinned_upstream"},
        )

        report = importer.import_rows([self.row(model="UNKNOWN")])

        self.assertEqual(report.rows[0].status, ImportStatus.INVALID)
        self.assertEqual(
            report.rows[0].reason,
            "device_type_not_found_in_pinned_upstream",
        )

    def test_12_apply_creates_device_with_tenant_site_and_safe_defaults(self):
        report = self.importer.import_rows([self.row()], apply=True)
        device = self.client.objects["/api/dcim/devices/"][0]
        self.assertEqual((device["tenant"], device["site"]), (1, 10))
        self.assertEqual(device["custom_fields"]["monitoring_enabled"], False)
        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_13_apply_creates_management_interface(self):
        self.importer.import_rows([self.row()], apply=True)
        interface = self.client.objects["/api/dcim/interfaces/"][0]
        self.assertEqual(interface["name"], "mgmt0")
        self.assertTrue(interface["mgmt_only"])

    def test_14_apply_assigns_ip_and_primary_ipv4(self):
        self.importer.import_rows([self.row()], apply=True)
        address = self.client.objects["/api/ipam/ip-addresses/"][0]
        device = self.client.objects["/api/dcim/devices/"][0]
        self.assertEqual(address["address"], "10.0.0.10/32")
        self.assertEqual(device["primary_ip4"], address["id"])

    def test_15_assigned_ip_conflict_is_detected_in_dry_run(self):
        self.client.objects["/api/ipam/ip-addresses/"].append({
            "id": 77, "address": "10.0.0.10/32", "vrf": None,
            "assigned_object_type": "dcim.interface", "assigned_object_id": 999,
        })
        report = self.importer.import_rows([self.row()])
        self.assertEqual(report.rows[0].status, ImportStatus.CONFLICT)
        self.assertEqual(report.rows[0].reason, "management_ip_assigned_elsewhere")

    def test_same_ip_in_another_vrf_does_not_conflict(self):
        vrf = {
            "id": 55, "name": "CUSTOMER-A", "rd": "65000:55",
            "tenant": {"id": self.tenant["id"]},
        }
        self.client.objects["/api/ipam/vrfs/"].append(vrf)
        self.client.objects["/api/ipam/ip-addresses/"].append({
            "id": 77, "address": "10.0.0.10/32", "vrf": None,
            "assigned_object_type": "dcim.interface", "assigned_object_id": 999,
        })

        report = self.importer.import_rows([self.row(vrf="65000:55")])

        self.assertEqual(report.rows[0].status, ImportStatus.CREATE)

    def test_overlapping_ip_is_created_once_per_tenant_vrf(self):
        tenant_b = {"id": 2, "name": "Customer B", "slug": "customer-b"}
        site_b = {
            "id": 12, "name": "Bratislava DC", "slug": "customer-b-bratislava-dc",
            "tenant": {"id": 2},
        }
        vrf_a = {"id": 51, "name": "acme-mgmt", "tenant": {"id": 1}}
        vrf_b = {"id": 52, "name": "customer-b-mgmt", "tenant": {"id": 2}}
        self.client.objects["/api/tenancy/tenants/"].append(tenant_b)
        self.client.objects["/api/dcim/sites/"].append(site_b)
        self.client.objects["/api/ipam/vrfs/"].extend([vrf_a, vrf_b])

        report = self.importer.import_rows([
            self.row(
                hostname="A-SW01", tenant="acme-slovakia", site="bratislava-dc",
                management_ip="192.168.1.10/24", vrf="acme-mgmt",
            ),
            self.row(
                number=3, hostname="B-SW01", tenant="customer-b",
                site="customer-b-bratislava-dc",
                management_ip="192.168.1.10/24", vrf="customer-b-mgmt",
            ),
        ], apply=True)

        self.assertEqual([row.status for row in report.rows], [ImportStatus.CREATE] * 2)
        addresses = self.client.objects["/api/ipam/ip-addresses/"]
        self.assertEqual(len(addresses), 2)
        self.assertEqual({item["vrf"] for item in addresses}, {51, 52})
        self.assertEqual({item["tenant"] for item in addresses}, {1, 2})
        prefixes = self.client.objects["/api/ipam/prefixes/"]
        self.assertEqual(len(prefixes), 2)
        self.assertEqual({item["vrf"] for item in prefixes}, {51, 52})

    def test_device_cannot_use_site_owned_by_another_tenant(self):
        other = {"id": 2, "name": "Customer B", "slug": "customer-b"}
        self.client.objects["/api/tenancy/tenants/"].append(other)
        report = self.importer.import_rows([
            self.row(tenant="customer-b", site="bratislava-dc")
        ])
        self.assertEqual(report.rows[0].status, ImportStatus.CONFLICT)
        self.assertEqual(report.rows[0].reason, "site_belongs_to_different_tenant")

    def test_16_second_apply_is_unchanged(self):
        first = self.importer.import_rows([self.row()], apply=True)
        writes_after_first = len(self.client.writes)
        second = self.importer.import_rows([self.row()], apply=True)
        self.assertEqual(first.rows[0].status, ImportStatus.CREATE)
        self.assertEqual(second.rows[0].status, ImportStatus.UNCHANGED)
        self.assertEqual(len(self.client.writes), writes_after_first)

    def test_17_partial_api_failure_is_isolated_per_row(self):
        self.client.fail_endpoint = "/api/dcim/devices/"
        report = self.importer.import_rows([
            self.row(),
            self.row(number=3, hostname="BAD", model="UNKNOWN", management_ip="10.0.0.11"),
        ], apply=True)
        self.assertEqual(report.rows[0].status, ImportStatus.ERROR)
        self.assertEqual(report.rows[1].status, ImportStatus.INVALID)

    def test_18_dry_run_has_no_writes_and_rejects_duplicate_rows(self):
        report = self.importer.import_rows([self.row(), self.row(number=3)])
        self.assertEqual(self.client.writes, [])
        self.assertEqual(report.rows[1].reason, "duplicate_input_row")


if __name__ == "__main__":
    unittest.main()
