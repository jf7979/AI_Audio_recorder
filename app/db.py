"""SQLite connection helper shared by the recorder, transcriber, and web processes.

Connections are meant to be short-lived (opened per operation/request, not held
open for the life of a process) so WAL checkpointing never gets blocked by a
long-running read transaction in another process.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    _configure(conn)
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def fts_integrity_check(db_path: Path) -> bool:
    """Runs FTS5's external-content integrity check. Returns True if clean."""
    conn = connect(db_path)
    try:
        try:
            conn.execute("INSERT INTO transcripts_fts(transcripts_fts) VALUES('integrity-check')")
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    finally:
        conn.close()
