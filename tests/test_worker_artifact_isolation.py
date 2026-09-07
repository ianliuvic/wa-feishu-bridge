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


if __name__ == "__main__":
    unittest.main()
