import importlib.util
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException


WORKER_PATH = Path(__file__).parents[1] / "codex-worker" / "server.py"
SPEC = importlib.util.spec_from_file_location("codex_worker_server", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(worker)


class WorkerArtifactIsolationTests(unittest.TestCase):
    def test_scoped_directory_is_inside_artifact_root(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            result = worker._resolve_artifact_root(
                workspace, "codex-artifacts/scheduled/task/run"
            )
            self.assertEqual(
                result,
                workspace / "codex-artifacts" / "scheduled" / "task" / "run",
            )
            self.assertTrue(result.is_dir())

    def test_shared_root_is_rejected_for_scoped_request(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with self.assertRaises(HTTPException):
                worker._resolve_artifact_root(workspace, "codex-artifacts")

    def test_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with self.assertRaises(HTTPException):
                worker._resolve_artifact_root(workspace, "../outside")

    def test_legacy_caller_keeps_shared_root(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            result = worker._resolve_artifact_root(workspace, None)
            self.assertEqual(result, workspace / "codex-artifacts")

    def test_task_complete_error_is_extracted(self):
        payload = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "error": {
                    "message": "stream disconnected before completion: stream closed before response.completed"
                },
            },
        }
        self.assertIn(
            "stream disconnected",
            worker._task_complete_error(__import__("json").dumps(payload)),
        )

    def test_stream_disconnect_is_retryable(self):
        self.assertTrue(
            worker._is_retryable_failure(
                "stream disconnected before completion: stream closed before response.completed"
            )
        )
        self.assertFalse(worker._is_retryable_failure("LinkedIn returned HTTP 401"))

    def test_run_logs_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            relative = worker._write_run_logs(
                workspace,
                "task-run-attempt-1",
                stdout="{\"type\":\"thread.started\"}\n",
                stderr="progress\n",
                metadata={"returncode": 1},
            )
            log_dir = workspace / relative
            self.assertTrue((log_dir / "stdout.jsonl").is_file())
            self.assertTrue((log_dir / "stderr.log").is_file())
            self.assertTrue((log_dir / "metadata.json").is_file())


if __name__ == "__main__":
    unittest.main()
