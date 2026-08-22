import tempfile
import unittest
from pathlib import Path

from app.attachments import PendingAttachmentStore


class PendingAttachmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "bridge.db"
        self.files_path = root / "attachments"
        self.store = PendingAttachmentStore(
            str(self.db_path),
            str(self.files_path),
            ttl_seconds=120,
            max_files_per_user=2,
            max_bytes=100,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_and_isolates_attachments_by_chat_and_sender(self):
        first = self.store.add(
            chat_id="chat-a",
            sender_id="user-a",
            source_message_id="message-a",
            message_type="image",
            file_name="dog.png",
            mime_type="image/png",
            data=b"png",
        )
        self.store.add(
            chat_id="chat-a",
            sender_id="user-b",
            source_message_id="message-b",
            message_type="file",
            file_name="brief.pdf",
            mime_type="application/pdf",
            data=b"pdf",
        )

        reopened = PendingAttachmentStore(str(self.db_path), str(self.files_path))
        mine = reopened.pop_for_user("chat-a", "user-a")
        self.assertEqual([item.file_name for item in mine], ["dog.png"])
        self.assertTrue(Path(first.local_path).exists())
        self.assertEqual(reopened.health()["pending"], 1)
        reopened.cleanup_files(mine)
        self.assertFalse(Path(first.local_path).exists())

    def test_cancel_removes_records_and_files(self):
        attachment = self.store.add(
            chat_id="chat-a",
            sender_id="user-a",
            source_message_id="message-a",
            message_type="file",
            file_name="notes.txt",
            mime_type="text/plain",
            data=b"notes",
        )
        self.assertEqual(self.store.cancel_for_user("chat-a", "user-a"), 1)
        self.assertFalse(Path(attachment.local_path).exists())
        self.assertEqual(self.store.health()["pending"], 0)

    def test_rejects_files_over_limit(self):
        with self.assertRaisesRegex(ValueError, "附件超过"):
            self.store.add(
                chat_id="chat-a",
                sender_id="user-a",
                source_message_id="message-a",
                message_type="file",
                file_name="large.bin",
                mime_type="application/octet-stream",
                data=b"x" * 101,
            )


if __name__ == "__main__":
    unittest.main()
