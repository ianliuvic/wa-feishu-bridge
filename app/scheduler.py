"""SQLite-backed durable scheduler and Feishu-to-Codex session mapping."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def next_run(cron: str, timezone_name: str, after: datetime | None = None) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    local_after = (after or utc_now()).astimezone(zone)
    try:
        value = croniter(cron, local_after).get_next(datetime)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cron expression: {cron}") from exc
    return value.astimezone(timezone.utc)


@dataclass
class ScheduledTask:
    id: str
    name: str
    prompt: str
    cron: str
    timezone: str
    chat_id: str
    enabled: bool
    next_run_at: str | None
    last_run_at: str | None
    last_status: str | None
    last_error: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict:
        return asdict(self)


class SchedulerStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    cron TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due
                    ON scheduled_tasks(enabled, next_run_at);

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    chat_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    response_preview TEXT,
                    error TEXT,
                    FOREIGN KEY(task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _task(row: sqlite3.Row) -> ScheduledTask:
        values = dict(row)
        values["enabled"] = bool(values["enabled"])
        return ScheduledTask(**values)

    def create_task(
        self, *, name: str, prompt: str, cron: str, timezone_name: str, chat_id: str
    ) -> ScheduledTask:
        run_at = next_run(cron, timezone_name)
        now = utc_text(utc_now())
        task_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO scheduled_tasks
                (id,name,prompt,cron,timezone,chat_id,enabled,next_run_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,1,?,?,?)""",
                (task_id, name, prompt, cron, timezone_name, chat_id, utc_text(run_at), now, now),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> ScheduledTask:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task(row)

    def list_tasks(self) -> list[ScheduledTask]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            ).fetchall()
        return [self._task(row) for row in rows]

    def set_enabled(self, task_id: str, enabled: bool) -> ScheduledTask:
        task = self.get_task(task_id)
        now = utc_now()
        run_at = next_run(task.cron, task.timezone, now) if enabled else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=?,next_run_at=?,updated_at=? WHERE id=?",
                (int(enabled), utc_text(run_at) if run_at else None, utc_text(now), task_id),
            )
        return self.get_task(task_id)

    def run_now(self, task_id: str) -> ScheduledTask:
        self.get_task(task_id)
        now = utc_text(utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=1,next_run_at=?,updated_at=? WHERE id=?",
                (now, now, task_id),
            )
        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> None:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        if result.rowcount == 0:
            raise KeyError(task_id)

    def claim_due(self, limit: int = 10) -> list[tuple[ScheduledTask, str]]:
        now = utc_now()
        claimed: list[tuple[ScheduledTask, str]] = []
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT * FROM scheduled_tasks
                WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?
                ORDER BY next_run_at LIMIT ?""",
                (utc_text(now), limit),
            ).fetchall()
            for row in rows:
                task = self._task(row)
                following = next_run(task.cron, task.timezone, now)
                run_id = str(uuid.uuid4())
                conn.execute(
                    """UPDATE scheduled_tasks
                    SET next_run_at=?,last_run_at=?,last_status='running',last_error=NULL,updated_at=?
                    WHERE id=?""",
                    (utc_text(following), utc_text(now), utc_text(now), task.id),
                )
                conn.execute(
                    "INSERT INTO task_runs (id,task_id,started_at,status) VALUES (?,?,?,'running')",
                    (run_id, task.id, utc_text(now)),
                )
                claimed.append((task, run_id))
            conn.commit()
        return claimed

    def finish_run(self, task_id: str, run_id: str, *, response: str = "", error: str = "") -> None:
        now = utc_text(utc_now())
        status = "failed" if error else "completed"
        with self._connect() as conn:
            conn.execute(
                """UPDATE scheduled_tasks
                SET last_status=?,last_error=?,updated_at=? WHERE id=?""",
                (status, error[:2000] or None, now, task_id),
            )
            conn.execute(
                """UPDATE task_runs
                SET finished_at=?,status=?,response_preview=?,error=? WHERE id=?""",
                (now, status, response[:2000] or None, error[:2000] or None, run_id),
            )

    def get_session(self, chat_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return row["session_id"] if row else None

    def set_session(self, chat_id: str, session_id: str, label: str | None = None) -> None:
        now = utc_text(utc_now())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO chat_sessions (chat_id,session_id,label,created_at,updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    label=COALESCE(excluded.label,chat_sessions.label),
                    updated_at=excluded.updated_at""",
                (chat_id, session_id, label, now, now),
            )

    def clear_session(self, chat_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_sessions WHERE chat_id=?", (chat_id,))

    def health(self) -> dict:
        with self._connect() as conn:
            task_count = conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]
            session_count = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "tasks": task_count,
            "sessions": session_count,
        }
