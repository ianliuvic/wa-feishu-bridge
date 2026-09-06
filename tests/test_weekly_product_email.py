import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT = (
    Path(__file__).parents[1]
    / "codex-worker"
    / "bundled-skills"
    / "hongxiu-weekly-product-email"
    / "scripts"
    / "weekly_product_email.py"
)
SPEC = importlib.util.spec_from_file_location("weekly_product_email", SCRIPT)
weekly = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = weekly
SPEC.loader.exec_module(weekly)


class WeeklyProductEmailTests(unittest.TestCase):
    def test_default_window_is_latest_complete_sunday_cutoff(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        period = weekly.resolve_week(
            None, now=datetime(2026, 9, 6, 9, 0, tzinfo=shanghai)
        )
        self.assertEqual(period.start.isoformat(), "2026-08-30T09:00:00+08:00")
        self.assertEqual(period.end.isoformat(), "2026-09-06T09:00:00+08:00")
        self.assertEqual(period.label, "2026-W36")

    def test_before_cutoff_uses_previous_complete_window(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        period = weekly.resolve_week(
            None, now=datetime(2026, 9, 6, 8, 59, tzinfo=shanghai)
        )
        self.assertEqual(period.start.isoformat(), "2026-08-23T09:00:00+08:00")
        self.assertEqual(period.end.isoformat(), "2026-08-30T09:00:00+08:00")
        self.assertEqual(period.label, "2026-W35")

    def test_explicit_window_start_must_be_sunday(self):
        period = weekly.resolve_week("2026-08-30")
        self.assertEqual(period.start.hour, 9)
        with self.assertRaises(weekly.WorkflowError):
            weekly.resolve_week("2026-08-31")

    def test_product_fingerprint_is_deterministic_and_order_sensitive(self):
        products = [{"wp_url": "https://example/a"}, {"wp_url": "https://example/b"}]
        self.assertEqual(
            weekly.products_fingerprint(products), weekly.products_fingerprint(list(products))
        )
        self.assertNotEqual(
            weekly.products_fingerprint(products), weekly.products_fingerprint(list(reversed(products)))
        )


if __name__ == "__main__":
    unittest.main()
