import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.scheduler import SchedulerStore, next_run


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


if __name__ == "__main__":
    unittest.main()
