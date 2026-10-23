"""The station's offline queue (its outbox) and saved settings, in a SQLite file.

Every scan is saved here before it's sent, and removed only once the API has answered.
So no scan is lost when the internet drops or the station restarts. Resending is safe:
a scan keeps its original scanned_at, which the API uses to ignore duplicates.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from core.scan import format_utc
from logic import Outgoing

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- increasing, so oldest first = order scanned
    code TEXT NOT NULL,
    action TEXT NOT NULL,
    staff TEXT,
    scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
CONFIG = "station_config"


class Outbox:
    def __init__(self, path: Path | str) -> None:
        # One connection, shared by the scan loop and the retry thread. sqlite3 objects
        # aren't safe to use from two threads at once, so the lock makes them take turns.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def add(self, scan: Outgoing) -> int:
        with self._lock, self._db:  # `with self._db` commits, so the scan is on disk
            cursor = self._db.execute(
                "INSERT INTO outbox (code, action, staff, scanned_at) VALUES (?, ?, ?, ?)",
                (scan.code, scan.action, scan.staff, format_utc(scan.scanned_at)),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def pending(self) -> list[tuple[int, Outgoing]]:
        """Waiting scans, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, code, action, staff, scanned_at FROM outbox ORDER BY id"
            ).fetchall()
        return [
            (row_id, Outgoing(code, action, datetime.fromisoformat(scanned_at), staff))
            for row_id, code, action, staff, scanned_at in rows
        ]

    def remove(self, row_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))

    def count(self) -> int:
        with self._lock:
            (count,) = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()
        return count

    def save_config(self, body: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                (CONFIG, json.dumps(body)),
            )

    def load_config(self) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM settings WHERE name = ?", (CONFIG,)
            ).fetchone()
        return json.loads(row[0]) if row else None
