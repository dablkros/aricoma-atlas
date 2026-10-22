import unittest

from atlas.prophylaxis.formatting import format_uptime_seconds


class UptimeFormattingTests(unittest.TestCase):
    def test_uptime_uses_human_readable_slovak_units(self):
        cases = (
            (0, "0 sekúnd"),
            (59, "59 sekúnd"),
            (60, "1 minúta"),
            (3 * 24 * 60 * 60, "3 dni"),
            (
                365 * 24 * 60 * 60 + 2 * 24 * 60 * 60 + 3 * 60 * 60 + 240,
                "1 rok 2 dni 3 hodiny 4 minúty",
            ),
        )
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(format_uptime_seconds(seconds), expected)

    def test_negative_or_boolean_uptime_is_rejected(self):
        for value in (-1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                format_uptime_seconds(value)


if __name__ == "__main__":
    unittest.main()
