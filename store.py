#!/usr/bin/env python3
"""Tiny SQLite store for settings and print history."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

DEFAULTS = {
    "target": "device:/dev/usb/lp0",
    "width_dots": 576,
    "encoding": "gbk",
    "chinese_mode": True,
    "cut": "partial",
    "feed_lines": 4,
    "threshold": 160,
    "font": "a",
}


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                bytes INTEGER NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                payload BLOB
            );
            """
        )
        self._db.commit()

    # ------------------------------------------------------------- settings
    def settings(self) -> dict:
        values = dict(DEFAULTS)
        with self._lock:
            for row in self._db.execute("SELECT key, value FROM settings"):
                try:
                    values[row["key"]] = json.loads(row["value"])
                except (ValueError, TypeError):
                    values[row["key"]] = row["value"]
        return values

    def save_settings(self, patch: dict) -> dict:
        with self._lock:
            for key, value in (patch or {}).items():
                if key in DEFAULTS:
                    self._db.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value)),
                    )
            self._db.commit()
        return self.settings()

    # -------------------------------------------------------------- history
    def add_history(self, kind: str, summary: str, size: int, target: str,
                    status: str, detail: str, payload: bytes = None) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO history(created_at, kind, summary, bytes, target, status, detail, payload)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"), kind, summary[:200], size, target,
                 status, (detail or "")[:400], payload),
            )
            self._db.commit()
            return int(cursor.lastrowid)

    def history(self, limit: int = 50) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, kind, summary, bytes, target, status, detail"
                " FROM history ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def payload(self, entry_id: int) -> bytes:
        with self._lock:
            row = self._db.execute("SELECT payload FROM history WHERE id = ?",
                                   (int(entry_id),)).fetchone()
        return bytes(row["payload"]) if row and row["payload"] else b""

    def clear_history(self) -> int:
        with self._lock:
            cursor = self._db.execute("DELETE FROM history")
            self._db.commit()
            return cursor.rowcount
