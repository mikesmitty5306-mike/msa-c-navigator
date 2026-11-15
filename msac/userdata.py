"""Personal data: saved trials and papers, private notes, and the home city.

Stored in its own SQLite file (config.USER_DB_PATH, default data/user.sqlite3), separate from
the research database, so "msac ingest" and rebuilding the research database never change it.
Only minimal personal data is kept: a home city, the saved items and the user's own notes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .geo import City
from .storage import Store

ITEM_TYPES = ("trial", "article")
NOTE_MAX = 2000
SCHEMA_VERSION = 1

_MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS saved_items (
        item_type TEXT NOT NULL CHECK (item_type IN ('trial', 'article')),
        item_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',     -- copy kept so the item still shows if it leaves the research data
        url TEXT,
        note TEXT NOT NULL DEFAULT '',
        saved_at TEXT NOT NULL,
        note_updated_at TEXT,
        last_status TEXT,                   -- trials: status seen at the latest ingest (or when saved)
        previous_status TEXT,               -- trials: status before the latest change
        status_changed_at TEXT,             -- trials: when that change was detected
        PRIMARY KEY (item_type, item_id)
    );
    """,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for v in sorted(k for k in _MIGRATIONS if k > version):
            with self.conn:
                self.conn.executescript(_MIGRATIONS[v])
                self.conn.execute(f"PRAGMA user_version = {int(v)}")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        with self.conn:
            if value is None:
                self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
            else:
                self.conn.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, json.dumps(value), _now()))

    def home_city(self) -> City | None:
        d = self.get_setting("home_city")
        return City.from_dict(d) if d else None

    def set_home_city(self, city: City | None) -> None:
        self.set_setting("home_city", city.to_dict() if city else None)

    # ------------------------------------------------------------ saved items
    def is_saved(self, item_type: str, item_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM saved_items WHERE item_type=? AND item_id=?",
                                 (item_type, item_id)).fetchone() is not None

    def saved_keys(self) -> set[str]:
        return {f"{r['item_type']}:{r['item_id']}" for r in self.conn.execute("SELECT item_type, item_id FROM saved_items")}

    def save(self, item_type: str, item_id: str, title: str, url: str | None, status: str | None = None) -> None:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"unknown item type: {item_type}")
        with self.conn:
            self.conn.execute(
                "INSERT INTO saved_items(item_type, item_id, title, url, saved_at, last_status) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(item_type, item_id) DO NOTHING",
                (item_type, item_id, title[:500], url, _now(), status))

    def unsave(self, item_type: str, item_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM saved_items WHERE item_type=? AND item_id=?", (item_type, item_id))

    def set_note(self, item_type: str, item_id: str, note: str) -> bool:
        """Set the private note on a saved item. Returns False if the item is not saved."""
        note = (note or "").replace("\r\n", "\n").strip()[:NOTE_MAX]
        with self.conn:
            cur = self.conn.execute("UPDATE saved_items SET note=?, note_updated_at=? WHERE item_type=? AND item_id=?",
                                    (note, _now(), item_type, item_id))
        return cur.rowcount == 1

    def get(self, item_type: str, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM saved_items WHERE item_type=? AND item_id=?",
                                 (item_type, item_id)).fetchone()

    def list_saved(self, item_type: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM saved_items WHERE item_type=? ORDER BY saved_at DESC",
                                 (item_type,)).fetchall()

    # ------------------------------------------------------------ trial status tracking
    def last_status_sync(self) -> str | None:
        return self.get_setting("last_status_sync_at")

    def sync_trial_statuses(self, store: Store) -> list[dict[str, str]]:
        """Run after each trial ingest: record status changes on saved trials.

        A change is stamped with this sync's time; the page shows a "status changed" badge for
        changes stamped with the most recent sync, so the badge means "changed in the last ingest".
        """
        # Microsecond precision so two syncs can never share a stamp (the badge compares stamps for equality).
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        changes = []
        with self.conn:
            for row in self.list_saved("trial"):
                t = store.get_trial(row["item_id"])
                if t is None or t.status == row["last_status"]:
                    continue
                if row["last_status"] is None:  # first time we see a status for it: nothing to compare
                    self.conn.execute("UPDATE saved_items SET last_status=? WHERE item_type='trial' AND item_id=?",
                                      (t.status, t.nct_id))
                    continue
                self.conn.execute(
                    "UPDATE saved_items SET previous_status=?, last_status=?, status_changed_at=?, title=? "
                    "WHERE item_type='trial' AND item_id=?",
                    (row["last_status"], t.status, now, t.title[:500], t.nct_id))
                changes.append({"id": t.nct_id, "title": t.title, "from": row["last_status"], "to": t.status})
        self.set_setting("last_status_sync_at", now)
        return changes

    def changed_in_last_sync(self, row: sqlite3.Row) -> bool:
        last = self.last_status_sync()
        return bool(last and row["status_changed_at"] and row["status_changed_at"] == last)
