"""Persistent per-user staging for Feishu attachments awaiting a text prompt."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .scheduler import utc_now, utc_text


@dataclass
class PendingAttachment:
    id: str
    chat_id: str
    sender_id: str
    source_message_id: str
    message_type: str
    file_name: str
    mime_type: str
    size: int
    local_path: str
    created_at: str
    expires_at: str


class PendingAttachmentStore:
    def __init__(
        self,
        db_path: str,
        root_dir: str,
        *,
        ttl_seconds: int = 120,
        max_files_per_user: int = 10,
        max_bytes: int = 50 * 1024 * 1024,
    ):
        self.db_path = Path(db_path)
        self.root_dir = Path(root_dir)
        self.ttl_seconds = max(30, ttl_seconds)
        self.max_files_per_user = max(1, max_files_per_user)
        self.max_bytes = max(1, max_bytes)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_attachments (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL UNIQUE,
                    message_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    local_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pending_attachments_owner
                    ON pending_attachments(chat_id, sender_id, expires_at);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> PendingAttachment:
        return PendingAttachment(**dict(row))

    @staticmethod
    def _safe_name(value: str) -> str:
        name = Path(value or "attachment").name
        name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name)
        return name[:180] or "attachment"

    def add(
        self,
        *,
        chat_id: str,
        sender_id: str,
        source_message_id: str,
        message_type: str,
        file_name: str,
        mime_type: str,
        data: bytes,
    ) -> PendingAttachment:
        if not data:
            raise ValueError("附件内容为空")
        if len(data) > self.max_bytes:
            raise ValueError(f"附件超过 {self.max_bytes // (1024 * 1024)}MB 限制")
        self.purge_expired()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM pending_attachments WHERE source_message_id=?",
                (source_message_id,),
            ).fetchone()
            if existing:
                return self._row(existing)
            count = conn.execute(
                "SELECT COUNT(*) FROM pending_attachments WHERE chat_id=? AND sender_id=?",
                (chat_id, sender_id),
            ).fetchone()[0]
            if count >= self.max_files_per_user:
                raise ValueError(f"待处理附件最多 {self.max_files_per_user} 个")

            attachment_id = str(uuid.uuid4())
            owner = hashlib.sha256(f"{chat_id}\n{sender_id}".encode()).hexdigest()[:24]
            directory = self.root_dir / owner
            directory.mkdir(parents=True, exist_ok=True)
            safe_name = self._safe_name(file_name)
            path = directory / f"{attachment_id}-{safe_name}"
            temporary = path.with_suffix(path.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(path)

            now = utc_now()
            expires = now + timedelta(seconds=self.ttl_seconds)
            values = (
                attachment_id,
                chat_id,
                sender_id,
                source_message_id,
                message_type,
                safe_name,
                mime_type or "application/octet-stream",
                len(data),
                str(path),
                utc_text(now),
                utc_text(expires),
            )
            conn.execute(
                """INSERT INTO pending_attachments
                (id,chat_id,sender_id,source_message_id,message_type,file_name,mime_type,
                 size,local_path,created_at,expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        return self.get(attachment_id)

    def get(self, attachment_id: str) -> PendingAttachment:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_attachments WHERE id=?", (attachment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        return self._row(row)

    def pop_for_user(self, chat_id: str, sender_id: str) -> list[PendingAttachment]:
        self.purge_expired()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT * FROM pending_attachments
                WHERE chat_id=? AND sender_id=? ORDER BY created_at""",
                (chat_id, sender_id),
            ).fetchall()
            conn.execute(
                "DELETE FROM pending_attachments WHERE chat_id=? AND sender_id=?",
                (chat_id, sender_id),
            )
            conn.commit()
        return [self._row(row) for row in rows]

    def cancel_for_user(self, chat_id: str, sender_id: str) -> int:
        attachments = self.pop_for_user(chat_id, sender_id)
        self.cleanup_files(attachments)
        return len(attachments)

    def purge_expired(self) -> int:
        now = utc_text(utc_now())
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_attachments WHERE expires_at<=?", (now,)
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM pending_attachments WHERE id=?",
                    [(row["id"],) for row in rows],
                )
        attachments = [self._row(row) for row in rows]
        self.cleanup_files(attachments)
        return len(attachments)

    @staticmethod
    def cleanup_files(attachments: list[PendingAttachment]) -> None:
        for attachment in attachments:
            try:
                Path(attachment.local_path).unlink(missing_ok=True)
            except OSError:
                pass

    def health(self) -> dict:
        self.purge_expired()
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM pending_attachments").fetchone()[0]
        return {
            "pending": count,
            "ttl_seconds": self.ttl_seconds,
            "max_files_per_user": self.max_files_per_user,
            "max_bytes": self.max_bytes,
        }
