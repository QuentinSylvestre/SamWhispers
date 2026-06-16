"""SQLite-backed transcription history.

Written by the worker process and read by the supervisor's web UI, so the
store uses WAL mode and short-lived per-call connections to stay safe across
both threads and processes. Volume is low (one row per dictation), so the
simplicity is worth more than connection pooling.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("samwhispers.history")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    language TEXT,
    text TEXT NOT NULL,
    translated_text TEXT,
    duration_ms INTEGER,
    cleanup_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transcriptions_created_at
    ON transcriptions (created_at DESC);
"""


def resolve_data_dir() -> Path:
    """Per-user data directory for SamWhispers (XDG / platform conventions)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "samwhispers"


def default_db_path() -> Path:
    """Default location of the history database."""
    return resolve_data_dir() / "history.db"


class HistoryStore:
    """Append-only-ish store of transcription results with search and pruning."""

    def __init__(self, path: Path | str | None = None, max_entries: int = 1000) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.max_entries = max_entries
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def add(
        self,
        text: str,
        *,
        language: str | None = None,
        duration_ms: int | None = None,
        cleanup_used: bool = False,
        translated_text: str | None = None,
    ) -> int:
        """Insert a transcription and prune to ``max_entries``. Returns the row id."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO transcriptions "
                "(created_at, language, text, translated_text, duration_ms, cleanup_used) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (created_at, language, text, translated_text, duration_ms, int(cleanup_used)),
            )
            row_id = int(cur.lastrowid or 0)
            if self.max_entries > 0:
                conn.execute(
                    "DELETE FROM transcriptions WHERE id NOT IN "
                    "(SELECT id FROM transcriptions ORDER BY id DESC LIMIT ?)",
                    (self.max_entries,),
                )
        return row_id

    def list(
        self,
        limit: int = 50,
        before_id: int | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Most-recent-first list with cursor pagination.

        ``before_id`` returns entries with ``id < before_id`` for stable paging.
        """
        limit = min(max(limit, 1), 100)
        where_parts: list[str] = []
        params: list[Any] = []

        if before_id is not None:
            where_parts.append("id < ?")
            params.append(before_id)

        search_where, search_params = self._search_clause(search)
        if search_where:
            # Strip the leading "WHERE "
            where_parts.append(search_where[6:])
            params.extend(search_params)

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM transcriptions {where} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self, search: str | None = None) -> int:
        where, params = self._search_clause(search)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM transcriptions {where}", params
            ).fetchone()
        return int(row["n"])

    def get(self, entry_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM transcriptions WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, entry_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM transcriptions WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def delete_batch(self, ids: list[int]) -> int:  # type: ignore[valid-type]
        """Atomically delete multiple entries. Raises ValueError if any ID is missing."""
        if not ids:
            raise ValueError("Empty ID list")
        unique_ids: list[int] = list(set(ids))
        if len(unique_ids) > 500:
            raise ValueError("Batch size exceeds maximum (500)")
        with self._connect() as conn:
            placeholders = ",".join("?" * len(unique_ids))
            existing = conn.execute(
                f"SELECT id FROM transcriptions WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            if len(existing) != len(unique_ids):
                found = {row["id"] for row in existing}
                missing = [i for i in unique_ids if i not in found]
                raise ValueError(f"IDs not found: {missing}")
            cur = conn.execute(
                f"DELETE FROM transcriptions WHERE id IN ({placeholders})",
                unique_ids,
            )
            return cur.rowcount

    def clear(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM transcriptions")
        return cur.rowcount

    @staticmethod
    def _search_clause(search: str | None) -> tuple[str, tuple[Any, ...]]:
        if not search:
            return "", ()
        # Escape LIKE wildcards so literal % and _ in user input aren't treated as patterns
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        return (
            "WHERE text LIKE ? ESCAPE '\\' OR translated_text LIKE ? ESCAPE '\\'",
            (like, like),
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["cleanup_used"] = bool(d["cleanup_used"])
        return d
