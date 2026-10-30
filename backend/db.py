"""SQLite persistence over a single shared connection."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence

from .security import hash_password
from .utils import utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL UNIQUE,
    agent_workspace TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'agent',
    sort_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'provisioning',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source_ext TEXT NOT NULL DEFAULT '.md',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    modified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(knowledge_base_id, name)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'incremental',
    phase TEXT NOT NULL DEFAULT 'queued',
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    created_by INTEGER REFERENCES admin_users(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_kb ON jobs(knowledge_base_id, created_at);
CREATE INDEX IF NOT EXISTS idx_documents_kb ON documents(knowledge_base_id, name);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    """Thread-safe database wrapper.

    SQLite runs in serialized mode, so one shared connection guarded by an
    RLock is both safe and far cheaper than the previous design, where every
    fetchone/fetchall/execute opened a brand-new connection (each one
    busy-waiting up to 30 s on lock contention). With per-operation
    connections a handful of concurrent requests could hold dozens of open
    connections and stall the whole app behind the write lock.
    """

    def __init__(self, path: Path):
        self.path = path
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            self._connection = connection
        return self._connection

    def connect(self) -> sqlite3.Connection:
        """Return the shared connection (kept for call-site compatibility)."""
        with self._lock:
            return self._conn()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def initialize(self) -> None:
        with self._lock:
            connection = self._conn()
            connection.executescript(SCHEMA)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(knowledge_bases)")
            }
            if "scope" not in columns:
                # Additive upgrade for databases created before public knowledge
                # bases existed; existing rows keep their agent-scoped meaning.
                connection.execute(
                    "ALTER TABLE knowledge_bases ADD COLUMN scope TEXT NOT NULL DEFAULT 'agent'"
                )
            if "sort_order" not in columns:
                # Additive upgrade for databases created before manual ordering.
                connection.execute(
                    "ALTER TABLE knowledge_bases ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
                )

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._conn()
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn().execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn().execute(sql, params).fetchall()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn().execute(sql, params)

    def seed_admin(self, username: str, password: str) -> None:
        if not username or not password:
            raise ValueError("administrator credentials are required")
        now = utc_now()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id FROM admin_users WHERE username = ?", (username,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO admin_users(username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), now, now),
                )

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM admin_users WHERE username = ?", (username,))

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM admin_users WHERE id = ?", (user_id,))

    def update_password(self, user_id: int, password_hash: str) -> None:
        self.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, utc_now(), user_id),
        )

    def create_session(
        self, token: str, user_id: int, csrf_token: str, expires_at: str
    ) -> None:
        self.execute(
            "INSERT INTO sessions(token, user_id, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, csrf_token, expires_at, utc_now()),
        )

    def get_session(self, token: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT s.*, u.username FROM sessions s JOIN admin_users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        )

    def delete_session(self, token: str) -> None:
        self.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def cleanup_sessions(self, now: str) -> None:
        self.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def get_setting(self, key: str) -> Optional[str]:
        row = self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )
