import csv
import tempfile
import unittest
from pathlib import Path

from atlas.inventory.parser import parse_inventory


class InventoryParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_csv(self):
        path = self.directory / "devices.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Hostname", "Hardware model", "Management IP", "Site"])
            writer.writerow(["BR-SW01", "C9500-48Y4C", "10.0.0.10", "Bratislava DC"])

        rows = parse_inventory(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].hostname, "BR-SW01")
        self.assertEqual(rows[0].model, "C9500-48Y4C")
        self.assertEqual(rows[0].site, "Bratislava DC")

    def test_import_xlsx(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl is installed from the pinned requirements in CI")
        path = self.directory / "devices.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Hostname", "Hardware model", "Management IP", "Tenant", "Site"])
        sheet.append(["ZA-SW01", "C9300L-24P-4G", "10.0.1.10/24", "acme", "zilina"])
        workbook.save(path)

        rows = parse_inventory(path)

        self.assertEqual(rows[0].management_ip, "10.0.1.10/24")
        self.assertEqual(rows[0].tenant, "acme")

    def test_missing_required_column_is_rejected(self):
        path = self.directory / "devices.csv"
        path.write_text("Hostname,Hardware model\nSW01,C9500-48Y4C\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "management_ip"):
            parse_inventory(path)

    def test_unsupported_file_type_is_rejected(self):
        path = self.directory / "devices.txt"
        path.write_text("fixture", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "csv or .xlsx"):
            parse_inventory(path)


if __name__ == "__main__":
    unittest.main()
