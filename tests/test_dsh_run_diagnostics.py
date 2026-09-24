import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException


WORKER_PATH = Path(__file__).parents[1] / "dsh-worker" / "server.py"
SPEC = importlib.util.spec_from_file_location("dsh_worker_server", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(worker)


class DshTailBufferTests(unittest.TestCase):
    def test_keeps_everything_below_the_limit(self):
        buffer = worker._TailBuffer(limit=1024)
        buffer.write(b"alpha")
        buffer.write(b"beta")
        self.assertEqual(buffer.text(), "alphabeta")
        self.assertFalse(buffer.truncated)
        self.assertEqual(buffer.tail(4), "beta")

    def test_keeps_the_tail_once_the_limit_is_passed(self):
        buffer = worker._TailBuffer(limit=10)
        for index in range(5):
            buffer.write(f"chunk{index}-".encode())
        text = buffer.text()
        self.assertTrue(buffer.truncated)
        self.assertIn("earlier output dropped", text)
        self.assertTrue(text.endswith("chunk4-"))
        self.assertNotIn("chunk0-", text)

    def test_decodes_partial_utf8_without_raising(self):
        buffer = worker._TailBuffer(limit=1024)
        buffer.write("泳装".encode("utf-8")[:4])
        self.assertIsInstance(buffer.text(), str)

    def test_a_timed_out_run_still_reports_its_transcript(self):
        # The timeout path calls text()/tail() after kill(); an empty result now
        # means the process wrote nothing, not that the buffer was discarded.
        buffer = worker._TailBuffer(limit=64)
        buffer.write(b"publishing article\n")
        self.assertEqual(buffer.tail(2000), "publishing article\n")


class DshArtifactServingTests(unittest.TestCase):
    def setUp(self):
        self._original = worker.DEFAULT_WORKSPACE
        self._temp = tempfile.TemporaryDirectory()
        worker.DEFAULT_WORKSPACE = Path(self._temp.name).resolve()

    def tearDown(self):
        worker.DEFAULT_WORKSPACE = self._original
        self._temp.cleanup()

    def _write(self, relative: str, body: str = "log") -> Path:
        path = worker.DEFAULT_WORKSPACE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def test_run_transcript_is_served(self):
        self._write("dsh-run-logs/run-1/stdout.txt", "publishing article")
        response = asyncio.run(worker.download_artifact("dsh-run-logs/run-1/stdout.txt"))
        self.assertTrue(str(response.path).endswith("stdout.txt"))

    def test_regular_artifact_is_still_served(self):
        relative = f"{worker.ARTIFACT_DIR_NAME}/scheduled/task/run/report.md"
        self._write(relative, "# report")
        response = asyncio.run(worker.download_artifact(relative))
        self.assertTrue(str(response.path).endswith("report.md"))

    def test_run_log_path_cannot_escape_its_root(self):
        self._write("secret.txt", "top secret")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(worker.download_artifact("dsh-run-logs/../secret.txt"))
        self.assertEqual(raised.exception.status_code, 404)

    def test_unknown_run_log_is_a_404(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(worker.download_artifact("dsh-run-logs/run-2/stdout.txt"))
        self.assertEqual(raised.exception.status_code, 404)

    def test_log_dir_from_the_error_detail_is_servable(self):
        # _write_run_logs returns this exact relative path, and the timeout error
        # hands it to the caller, so it has to resolve through the endpoint.
        relative = worker._write_run_logs(
            worker.DEFAULT_WORKSPACE, "run-3", stdout="hello", stderr="warn", metadata={}
        )
        self.assertEqual(relative, "dsh-run-logs/run-3")
        response = asyncio.run(worker.download_artifact(f"{relative}/stdout.txt"))
        self.assertTrue(str(response.path).endswith("stdout.txt"))


if __name__ == "__main__":
    unittest.main()
