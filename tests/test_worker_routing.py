"""Executor routing: the worker column, its migration, and endpoint resolution."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import worker_endpoint
from app.scheduler import SchedulerStore


class WorkerColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "tasks.db"
        self.store = SchedulerStore(str(self.db))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_defaults_to_codex(self):
        task = self.store.create_task(
            name="t", prompt="p", cron="0 10 * * *",
            timezone_name="Asia/Shanghai", chat_id="oc_1",
        )
        self.assertEqual(task.worker, "codex")

    def test_create_and_update_worker(self):
        task = self.store.create_task(
            name="t", prompt="p", cron="0 10 * * *",
            timezone_name="Asia/Shanghai", chat_id="oc_1", worker="dsh",
        )
        self.assertEqual(task.worker, "dsh")

        moved = self.store.update_task(
            task.id, name="t", prompt="p", cron="0 10 * * *",
            timezone_name="Asia/Shanghai", chat_id="oc_1", worker="codex",
        )
        self.assertEqual(moved.worker, "codex")

    def test_unknown_worker_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_task(
                name="t", prompt="p", cron="0 10 * * *",
                timezone_name="Asia/Shanghai", chat_id="oc_1", worker="gpt5",
            )

    def test_existing_database_gains_the_column_as_codex(self):
        """A database created before routing must migrate to codex, not fail."""
        legacy = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy) as conn:
            conn.executescript(
                """
                CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
                    cron TEXT NOT NULL, timezone TEXT NOT NULL, chat_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT, last_run_at TEXT,
                    last_status TEXT, last_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO scheduled_tasks
                    (id,name,prompt,cron,timezone,chat_id,enabled,created_at,updated_at)
                VALUES ('old','legacy','p','0 10 * * *','Asia/Shanghai','oc_1',1,'x','x');
                """
            )

        migrated = SchedulerStore(str(legacy))
        task = migrated.get_task("old")
        self.assertEqual(task.worker, "codex")
        rows = migrated.list_tasks()
        self.assertEqual(len(rows), 1)

    def test_as_dict_exposes_worker(self):
        task = self.store.create_task(
            name="t", prompt="p", cron="0 10 * * *",
            timezone_name="Asia/Shanghai", chat_id="oc_1", worker="dsh",
        )
        self.assertEqual(task.as_dict()["worker"], "dsh")


class WorkerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("CODEX_WORKER_URL", "CODEX_WORKER_TOKEN",
                       "DSH_WORKER_URL", "DSH_WORKER_TOKEN", "DEFAULT_WORKER")}
        os.environ.update({
            "CODEX_WORKER_URL": "https://codex.example",
            "CODEX_WORKER_TOKEN": "codex-token",
            "DSH_WORKER_URL": "https://dsh.example",
            "DSH_WORKER_TOKEN": "dsh-token",
            "DEFAULT_WORKER": "codex",
        })
        import importlib

        import app.config

        self.config = importlib.reload(app.config)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import importlib

        import app.config

        importlib.reload(app.config)

    def test_resolves_each_worker(self):
        self.assertEqual(self.config.worker_endpoint("codex"), (
            "codex", "https://codex.example", "codex-token"))
        self.assertEqual(self.config.worker_endpoint("dsh"), (
            "dsh", "https://dsh.example", "dsh-token"))

    def test_blank_and_none_use_the_default(self):
        self.assertEqual(self.config.worker_endpoint(None)[0], "codex")
        self.assertEqual(self.config.worker_endpoint("")[0], "codex")

    def test_unknown_worker_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.config.worker_endpoint("gemini")

    def test_unconfigured_worker_is_rejected(self):
        os.environ["DSH_WORKER_URL"] = ""
        self.config = __import__("importlib").reload(self.config)
        with self.assertRaises(RuntimeError):
            self.config.worker_endpoint("dsh")


if __name__ == "__main__":
    unittest.main()
