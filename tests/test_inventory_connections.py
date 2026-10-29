import copy
import unittest

from atlas.inventory.connections import ConnectionMappingError, ConnectionResolver


class FakeNetBox:
    def __init__(self):
        tenant = {"id": 1, "name": "Customer A", "slug": "customer-a"}
        self.objects = {
            "/api/dcim/devices/": [{
                "id": 10,
                "name": "edge-a",
                "tenant": tenant,
                "site": {"id": 20},
                "primary_ip4": {"id": 30},
                "custom_fields": {},
            }],
            "/api/dcim/sites/": [{
                "id": 20,
                "name": "Bratislava",
                "slug": "customer-a-bratislava",
                "tenant": tenant,
                "custom_fields": {"atlas_connection_id": "customer-a-primary"},
            }],
            "/api/ipam/ip-addresses/": [{
                "id": 30,
                "address": "192.168.1.10/24",
                "tenant": tenant,
                "vrf": {"id": 40},
            }],
            "/api/ipam/vrfs/": [{
                "id": 40,
                "name": "customer-a-mgmt",
                "rd": None,
                "tenant": tenant,
            }],
        }

    @staticmethod
    def _id(value):
        return value.get("id") if isinstance(value, dict) else value

    def get_all(self, endpoint, params=None):
        params = params or {}
        return [
            copy.deepcopy(item) for item in self.objects[endpoint]
            if all(self._id(item.get(key)) == value for key, value in params.items())
        ]


class ConnectionResolverTests(unittest.TestCase):
    def setUp(self):
        self.netbox = FakeNetBox()
        self.config = {"connection_profiles": {
            "customer-a-primary": {
                "tenant": "customer-a",
                "status": "planned",
                "vrf": "customer-a-mgmt",
                "transport": "ipsec",
            },
            "customer-a-device": {
                "tenant": "customer-a",
                "status": "active",
                "vrf": "40",
                "transport": "ipsec",
            },
        }}

    def test_site_connection_resolves_complete_context(self):
        result = ConnectionResolver(self.netbox, self.config).resolve(10)
        self.assertEqual(result["tenant"]["slug"], "customer-a")
        self.assertEqual(result["site"]["slug"], "customer-a-bratislava")
        self.assertEqual(result["management_ip"], "192.168.1.10")
        self.assertEqual(result["vrf"]["name"], "customer-a-mgmt")
        self.assertEqual(result["connection_id"], "customer-a-primary")
        self.assertEqual(result["connection_source"], "site")

    def test_device_connection_overrides_site(self):
        self.netbox.objects["/api/dcim/devices/"][0]["custom_fields"] = {
            "atlas_connection_id": "customer-a-device"
        }
        result = ConnectionResolver(self.netbox, self.config).resolve(10)
        self.assertEqual(result["connection_id"], "customer-a-device")
        self.assertEqual(result["connection_source"], "device")

    def test_missing_connection_is_unmapped_without_fallback(self):
        self.netbox.objects["/api/dcim/sites/"][0]["custom_fields"] = {}
        result = ConnectionResolver(self.netbox, self.config).resolve(10)
        self.assertEqual(result["connection_status"], "unmapped")
        self.assertIsNone(result["connection_id"])

    def test_cross_tenant_connection_is_rejected(self):
        self.config["connection_profiles"]["customer-a-primary"]["tenant"] = "customer-b"
        with self.assertRaisesRegex(ConnectionMappingError, "tenant_mismatch"):
            ConnectionResolver(self.netbox, self.config).resolve(10)

    def test_tenant_scoped_resolution_does_not_return_other_customer(self):
        with self.assertRaisesRegex(ConnectionMappingError, "outside_requested_tenant"):
            ConnectionResolver(self.netbox, self.config).resolve(
                10, expected_tenant="customer-b"
            )

    def test_connection_for_wrong_vrf_is_rejected(self):
        self.config["connection_profiles"]["customer-a-primary"]["vrf"] = "other-vrf"
        with self.assertRaisesRegex(ConnectionMappingError, "vrf_mismatch"):
            ConnectionResolver(self.netbox, self.config).resolve(10)


if __name__ == "__main__":
    unittest.main()
