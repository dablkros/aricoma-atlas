import os
import tempfile
import unittest
from pathlib import Path

from atlas.prophylaxis.models import CPUCheckResult, CPUUtilizationValues
from atlas.prophylaxis.store import ResultStoreError, SQLiteResultStore


class SQLiteResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "nested/results.sqlite3"
        self.store = SQLiteResultStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_success_and_error_results_persist_in_newest_first_order(self):
        success = self.store.save(
            CPUCheckResult(
                device="SW01",
                platform="cisco-ios-xe",
                status="ok",
                values=CPUUtilizationValues(
                    current_percent=8,
                    one_minute_percent=6,
                    five_minute_percent=5,
                ),
            )
        )
        failure = self.store.save(
            CPUCheckResult(
                device="FW01",
                platform="fortios",
                status="error",
                error="authentication_failed",
            )
        )

        history = SQLiteResultStore(self.path).list(20)

        self.assertEqual([item.id for item in history], [failure.id, success.id])
        self.assertEqual(history[0].error, "authentication_failed")
        self.assertEqual(history[1].values.current_percent, 8.0)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_history_limit_is_enforced(self):
        for index in range(3):
            self.store.save(
                CPUCheckResult(
                    device=f"SW{index}",
                    platform="cisco-ios",
                    status="ok",
                    values=CPUUtilizationValues(current_percent=index),
                )
            )

        self.assertEqual(len(self.store.list(2)), 2)
        with self.assertRaises(ValueError):
            self.store.list(101)

    def test_retention_prunes_oldest_results(self):
        store = SQLiteResultStore(self.path, max_results=100)
        for index in range(101):
            store.save(
                CPUCheckResult(
                    device=f"SW{index}",
                    platform="cisco-ios",
                    status="ok",
                    values=CPUUtilizationValues(current_percent=index % 100),
                )
            )

        history = store.list(100)
        self.assertEqual(len(history), 100)
        self.assertEqual(history[-1].device, "SW1")

    def test_database_symlink_is_rejected(self):
        target = Path(self.temporary.name) / "outside.sqlite3"
        target.write_bytes(b"")
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(target)

        with self.assertRaises(ResultStoreError):
            self.store.list()


if __name__ == "__main__":
    unittest.main()
