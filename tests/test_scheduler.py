import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.scheduler import (
    MD_ONLY_DELIVERY_MARKER,
    SchedulerStore,
    next_run,
    select_delivery_artifacts,
)


class SchedulerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SchedulerStore(str(Path(self.temp_dir.name) / "tasks.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_pause_resume_and_delete(self):
        task = self.store.create_task(
            name="daily ad",
            prompt="create one ad",
            cron="0 10 * * *",
            timezone_name="Asia/Shanghai",
            chat_id="oc_marketing",
        )
        self.assertTrue(task.enabled)
        self.assertIsNotNone(task.next_run_at)

        paused = self.store.set_enabled(task.id, False)
        self.assertFalse(paused.enabled)
        self.assertIsNone(paused.next_run_at)

        resumed = self.store.set_enabled(task.id, True)
        self.assertTrue(resumed.enabled)
        self.assertIsNotNone(resumed.next_run_at)

        self.store.delete_task(task.id)
        self.assertEqual(self.store.list_tasks(), [])

    def test_chat_session_mapping(self):
        self.assertIsNone(self.store.get_session("oc_1"))

    def test_update_preserves_identity_and_run_history(self):
        task = self.store.create_task(
            name="daily report",
            prompt="complete days",
            cron="30 8 * * *",
            timezone_name="Asia/Shanghai",
            chat_id="oc_marketing",
        )
        updated = self.store.update_task(
            task.id,
            name=task.name,
            prompt="include today through run time",
            cron=task.cron,
            timezone_name=task.timezone,
            chat_id=task.chat_id,
        )
        self.assertEqual(updated.id, task.id)
        self.assertEqual(updated.prompt, "include today through run time")
        self.assertEqual(updated.created_at, task.created_at)
        self.assertTrue(updated.enabled)
        self.assertIsNotNone(updated.next_run_at)
        self.store.set_session("oc_1", "session-a")
        self.assertEqual(self.store.get_session("oc_1"), "session-a")
        self.store.set_session("oc_1", "session-b")
        self.assertEqual(self.store.get_session("oc_1"), "session-b")
        self.store.clear_session("oc_1")
        self.assertIsNone(self.store.get_session("oc_1"))

    def test_next_run_respects_timezone(self):
        after = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
        value = next_run("0 10 * * *", "Asia/Shanghai", after)
        self.assertEqual(value, datetime(2026, 8, 22, 2, 0, tzinfo=timezone.utc))

    def test_md_only_delivery_filters_json_and_scripts(self):
        artifacts = [
            {"name": "report.md", "path": "report.md"},
            {"name": "data.json", "path": "data.json"},
            {"name": "collect.py", "path": "collect.py"},
        ]
        md_only, selected = select_delivery_artifacts(
            f"{MD_ONLY_DELIVERY_MARKER}\nCreate the daily report.", artifacts
        )
        self.assertTrue(md_only)
        self.assertEqual(selected, [artifacts[0]])

    def test_md_only_delivery_sends_only_one_report(self):
        artifacts = [
            {"name": "draft.md", "path": "draft.md", "size": 100},
            {"name": "industry-news-report.md", "path": "industry-news-report.md", "size": 900},
            {"name": "notes.md", "path": "notes.md", "size": 4000},
        ]
        md_only, selected = select_delivery_artifacts(MD_ONLY_DELIVERY_MARKER, artifacts)
        self.assertTrue(md_only)
        self.assertEqual([item["name"] for item in selected], ["industry-news-report.md"])

    def test_md_only_falls_back_to_the_largest_markdown(self):
        artifacts = [
            {"name": "a.md", "path": "a.md", "size": 10},
            {"name": "b.md", "path": "b.md", "size": 900},
        ]
        _, selected = select_delivery_artifacts(MD_ONLY_DELIVERY_MARKER, artifacts)
        self.assertEqual([item["name"] for item in selected], ["b.md"])

    def test_md_only_without_markdown_selects_nothing(self):
        md_only, selected = select_delivery_artifacts(
            MD_ONLY_DELIVERY_MARKER, [{"name": "data.json", "path": "data.json"}]
        )
        self.assertTrue(md_only)
        self.assertEqual(selected, [])

    def test_default_delivery_keeps_all_artifacts(self):
        artifacts = [{"name": "data.json", "path": "data.json"}]
        md_only, selected = select_delivery_artifacts("Create the report.", artifacts)
        self.assertFalse(md_only)
        self.assertEqual(selected, artifacts)


if __name__ == "__main__":
    unittest.main()
