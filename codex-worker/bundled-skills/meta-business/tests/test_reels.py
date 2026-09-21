from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import crosspost
import instagram
import media_stage
import pages


class FakePageClient:
    def __init__(self):
        self.calls = []

    def post(self, path, payload):
        self.calls.append(("post", path, payload.copy()))
        if payload.get("upload_phase") == "start":
            return {"video_id": "12345"}
        return {"success": True, "post_id": "page_12345"}

    def upload_reel_from_url(self, video_id, url):
        self.calls.append(("video", video_id, url))
        return {"success": True}

    def upload_file(self, path, source, field, mime, params):
        self.calls.append(("cover", path, field, mime, params.copy()))
        return {"success": True, "is_preferred": True}

    def get(self, video_id, params):
        self.calls.append(("get", video_id, params.copy()))
        return {"id": video_id, "permalink_url": "https://facebook.example/reel/12345", "status": {"video_status": "ready"}}


class FakeInstagramClient:
    def __init__(self):
        self.calls = []

    def post(self, path, payload):
        self.calls.append(("post", path, payload.copy()))
        return {"id": "67890"}

    def get(self, path, params):
        self.calls.append(("get", path, params.copy()))
        if params.get("fields") == instagram.STATUS_FIELDS:
            return {"id": path, "status_code": "FINISHED"}
        return {
            "id": path, "media_type": "VIDEO", "media_product_type": "REELS",
            "permalink": "https://instagram.example/reel/67890", "thumbnail_url": "https://example/cover.jpg",
        }


class ReelWorkflowTests(unittest.TestCase):
    def test_page_reel_uploads_cover_before_finish(self):
        client = FakePageClient()
        with tempfile.TemporaryDirectory() as folder:
            cover = Path(folder) / "cover.jpg"
            cover.write_bytes(b"jpeg")
            args = argparse.Namespace(
                confirm_publish=True, dry_run=False, url="https://example/video.mp4", file=None,
                cover_file=str(cover), cover_url=None, description="caption", title=None, interval=1, timeout=10,
            )
            with patch.object(pages, "resolve_public_media", return_value=(args.url, {"source": "url"})):
                result = pages.publish_page_reel(client, "999", args)
        kinds = [call[0] for call in client.calls]
        self.assertLess(kinds.index("video"), kinds.index("cover"))
        finish_index = next(i for i, call in enumerate(client.calls) if call[0] == "post" and call[2].get("upload_phase") == "finish")
        self.assertLess(kinds.index("cover"), finish_index)
        self.assertTrue(result["cover_preferred"])
        self.assertEqual(result["permalink"], "https://facebook.example/reel/12345")

    def test_instagram_reel_returns_standard_verification(self):
        client = FakeInstagramClient()
        result = instagram.publish_instagram_reel(
            client, "999", {"media_type": "REELS", "video_url": "https://example/video.mp4"}, 1, 10
        )
        self.assertEqual(result["verification"]["media_product_type"], "REELS")
        self.assertIn("permalink", result["verification"])

    def test_instagram_caption_rejects_clickable_url(self):
        with self.assertRaisesRegex(Exception, "must not contain URLs"):
            instagram.reel_payload("https://example.com/video.mp4", "Shop https://example.com", True)

    def test_instagram_caption_accepts_link_in_bio(self):
        payload = instagram.reel_payload("https://example.com/video.mp4", "Shop via the link in bio.", True)
        self.assertEqual(payload["caption"], "Shop via the link in bio.")

    def test_windows_drive_path_is_local(self):
        self.assertFalse(media_stage._is_url(r"C:\media\video.mp4"))
        self.assertTrue(media_stage._is_url("https://example.com/video.mp4"))

    def test_completed_operation_is_not_republished(self):
        operation = {
            "schema_version": 1, "status": "completed", "platforms": {"instagram": {"status": "completed"}}
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operation.json"
            crosspost._atomic_write(path, operation)
            loaded = crosspost._load_operation(path)
        self.assertEqual(loaded["platforms"]["instagram"]["status"], "completed")

    def test_ambiguous_operation_requires_explicit_retry(self):
        operation = {
            "schema_version": 1, "input": {"video": "https://example/video.mp4", "cover": None},
            "targets": {"page_id": "1", "ig_user_id": "2"}, "platforms": {"page": {"status": "unknown"}},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operation.json"
            with self.assertRaisesRegex(Exception, "ambiguous"):
                crosspost.run_operation(operation, path, False, 1, 1)


if __name__ == "__main__":
    unittest.main()
