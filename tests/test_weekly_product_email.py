import importlib.util
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
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

    def test_rendered_email_never_uses_recipient_name(self):
        period = weekly.resolve_week("2026-08-30")
        product = {
            "title": "Test swimsuit",
            "image_url": "https://example.test/image.webp",
            "wp_url": "https://example.test/product/",
            "listing_time": "2026-09-01T00:00:00Z",
        }
        rendered = weekly.render_email(period, [product, {**product, "wp_url": "https://example.test/product-2/"}])
        self.assertNotIn("$[FNAME", rendered)
        self.assertNotIn("Hi ", rendered)
        self.assertIn("This week, we added 2 new swimwear styles", rendered)
        self.assertIn("$[LI:UNSUBSCRIBE]$", rendered)

    def test_rendered_content_version_changes_with_template_content(self):
        period = weekly.resolve_week("2026-08-30")
        product = {
            "title": "Test swimsuit",
            "image_url": "https://example.test/image.webp",
            "wp_url": "https://example.test/product/",
            "listing_time": "2026-09-01T00:00:00Z",
        }
        rendered = weekly.render_email(period, [product])
        version = weekly.rendered_content_version(rendered)
        self.assertEqual(len(version), 16)
        self.assertIn(f"CONTENT-VERSION:{version}", rendered)
        self.assertEqual(
            weekly.versioned_content_url("https://example.test/email/", version),
            f"https://example.test/email/?v={version}",
        )

    def test_discover_products_accepts_products_without_shop_listing_time(self):
        period = weekly.resolve_week("2026-08-30")
        payload = {"products": [
            {"title": "A", "image_url": "https://example.test/a.jpg",
             "wp_url": "https://example.test/a"},
            {"title": "B", "image_url": "https://example.test/b.jpg",
             "wp_url": "https://example.test/b", "listing_time": "2026-09-01T00:00:00Z"},
        ]}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        env = {"COLLECTOR_API_KEY": "test-key", "COLLECTOR_API_URL": "https://collector.test"}
        with patch.object(weekly, "require_config"), patch.object(
            weekly, "env", side_effect=lambda name, default="": env.get(name, default)
        ), patch.object(weekly.subprocess, "run", return_value=completed):
            products = weekly.discover_products(period, 30)
        self.assertEqual(
            [product["wp_url"] for product in products],
            ["https://example.test/a", "https://example.test/b"],
        )

    def test_discover_products_rejects_missing_public_url(self):
        period = weekly.resolve_week("2026-08-30")
        payload = {"products": [{"title": "A", "image_url": "https://example.test/a.jpg"}]}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        env = {"COLLECTOR_API_KEY": "test-key", "COLLECTOR_API_URL": "https://collector.test"}
        with patch.object(weekly, "require_config"), patch.object(
            weekly, "env", side_effect=lambda name, default="": env.get(name, default)
        ), patch.object(weekly.subprocess, "run", return_value=completed):
            with self.assertRaises(weekly.WorkflowError):
                weekly.discover_products(period, 30)

    def test_normalize_product_image_crops_every_ratio_to_one_frame(self):
        from PIL import Image
        from io import BytesIO

        for size in ((1000, 1000), (600, 900), (900, 600)):
            buffer = BytesIO()
            Image.new("RGB", size, "white").save(buffer, format="PNG")
            data = weekly.normalize_product_image(buffer.getvalue())
            image = Image.open(BytesIO(data))
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, weekly.EMAIL_IMAGE_SIZE)

    def test_image_key_stays_unique_for_reused_style_numbers(self):
        first = {"style_no": "SWOP712", "product_detail_id": "2680"}
        second = {"style_no": "SWOP712", "product_detail_id": "2681"}
        self.assertNotEqual(weekly.image_key(first), weekly.image_key(second))
        self.assertEqual(weekly.image_key(first), "SWOP712-2680")

    def test_prepare_images_rewrites_urls_and_keeps_broken_images(self):
        from PIL import Image
        from io import BytesIO

        buffer = BytesIO()
        Image.new("RGB", (1200, 1200), "white").save(buffer, format="PNG")
        good = {"style_no": "A1", "product_detail_id": "1",
                "image_url": "https://example.test/a.png"}
        bad = {"style_no": "B2", "product_detail_id": "2",
               "image_url": "https://example.test/b.png"}
        period = weekly.resolve_week("2026-08-30")

        def fake_request(method, url, **kwargs):
            if url.endswith("b.png"):
                raise weekly.WorkflowError("boom")
            return 200, buffer.getvalue(), {}

        with patch.object(weekly, "request", side_effect=fake_request), patch.object(
            weekly, "env", side_effect=lambda name, default="": (
                "https://email.test" if name == "EMAIL_CAMPAIGN_BASE_URL" else default)
        ):
            prepared, files, warnings = weekly.prepare_images(period, [good, bad])

        self.assertEqual(prepared[0]["image_url"],
                         "https://email.test/campaigns/2026-W36-new-arrivals/images/A1-1.jpg")
        self.assertEqual(prepared[1]["image_url"], "https://example.test/b.png")
        self.assertEqual(list(files), ["campaigns/2026-W36-new-arrivals/images/A1-1.jpg"])
        self.assertEqual(len(warnings), 1)

    def test_clamp_text_cuts_long_copy_on_a_word_boundary(self):
        text = "Cream Botanical Floral Triangle Halter Bikini with Ruffle Trim and Tie-Side Bottoms"
        clamped = weekly.clamp_text(text, weekly.TITLE_MAX_CHARS)
        self.assertLessEqual(len(clamped), weekly.TITLE_MAX_CHARS)
        self.assertTrue(clamped.endswith("…"))
        self.assertFalse(clamped.rstrip("…").endswith(" "))
        self.assertEqual(weekly.clamp_text("Short name", weekly.TITLE_MAX_CHARS), "Short name")

    def test_render_uses_fixed_text_blocks(self):
        period = weekly.resolve_week("2026-08-30")
        long_title = "Cream Botanical Floral Triangle Halter Bikini with Ruffle Trim and Tie-Side Bottoms"
        product = {
            "title": long_title,
            "description": "A long description " * 12,
            "image_url": "https://example.test/image.jpg",
            "wp_url": "https://example.test/product/",
            "style_no": "TEST1",
        }
        rendered = weekly.render_products([product])
        self.assertIn(f'height="{weekly.TITLE_HEIGHT}"', rendered)
        self.assertIn(f'height="{weekly.DESCRIPTION_HEIGHT}"', rendered)
        self.assertNotIn(long_title, rendered)
        self.assertIn("…", rendered)

    def test_missing_zoho_campaign_is_recoverable(self):
        with patch.object(
            weekly,
            "request_json",
            return_value={"code": "2206", "status": "error"},
        ), patch.object(weekly, "zoho_access_token", return_value="token"):
            self.assertEqual(weekly.zoho_campaign_status("deleted-key"), "Missing")

    def test_delete_draft_uses_china_v11_get_endpoint(self):
        with patch.object(weekly, "zoho_campaign_status", return_value="Draft"), patch.object(
            weekly, "zoho_access_token", return_value="token"
        ), patch.object(
            weekly, "request_json", return_value={"code": "0", "status": "success"}
        ) as request_json:
            weekly.delete_zoho_draft("campaign-key")
        method, url = request_json.call_args.args[:2]
        self.assertEqual(method, "GET")
        self.assertIn("/deletecampaign?", url)
        self.assertIn("campaignkey=campaign-key", url)


if __name__ == "__main__":
    unittest.main()
