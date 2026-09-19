"""Writes a finished speech segment to disk (FLAC) and inserts its DB row.

recordings.file_path is stored RELATIVE to config.storage.data_dir, so the
whole data directory can be moved/backed up without invalidating the DB.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import soundfile as sf

from app.config import Config
from app.time_utils import parse_iso

logger = logging.getLogger("recorder")


def resolve_session_id(conn: sqlite3.Connection, started_at: datetime, gap_minutes: int) -> int:
    """Assigns a session_id for a NEW recording being inserted in real time.

    This assumes rows are inserted in chronological order (true for the live
    recorder, which is the only caller in the normal flow) - it looks at
    whichever row was inserted last, not the chronologically-nearest one.
    Out-of-order historical inserts (orphan reconciliation) must NOT rely on
    this; use renumber_sessions() afterward instead, since a locally-computed
    session_id for a row inserted out of order can collide with an unrelated,
    already-existing session number."""
    row = conn.execute(
        "SELECT session_id, ended_at FROM recordings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 1
    last_end = parse_iso(row["ended_at"])
    if started_at - last_end > timedelta(minutes=gap_minutes):
        return row["session_id"] + 1
    return row["session_id"]


def renumber_sessions(conn: sqlite3.Connection, gap_minutes: int) -> None:
    """Recomputes session_id for every recording from scratch, in chronological
    order. The only correct way to assign session numbers once rows may have
    been inserted out of chronological order (orphan reconciliation) - a
    locally-computed session_id for one out-of-order row can't be trusted not
    to collide with an unrelated session that already exists at a later id."""
    rows = conn.execute(
        "SELECT id, session_id, started_at, ended_at FROM recordings ORDER BY started_at ASC"
    ).fetchall()

    session_id = 0
    last_end: datetime | None = None
    updates = []
    for row in rows:
        started_at = parse_iso(row["started_at"])
        ended_at = parse_iso(row["ended_at"])
        if last_end is None or started_at - last_end > timedelta(minutes=gap_minutes):
            session_id += 1
        # Only write rows that actually change - normally almost nothing does,
        # and rewriting every row would hold the write lock (and bloat the WAL)
        # for no reason while the recorder is trying to insert.
        if row["session_id"] != session_id:
            updates.append((session_id, row["id"]))
        last_end = ended_at if last_end is None else max(last_end, ended_at)

    if updates:
        conn.executemany("UPDATE recordings SET session_id = ? WHERE id = ?", updates)
        conn.commit()
        logger.info("Renumbered %d recording(s) into sessions", len(updates))


def finalize_segment(
    config: Config,
    conn: sqlite3.Connection,
    samples: np.ndarray,
    sample_rate: int,
    started_at: datetime,
    ended_at: datetime,
) -> int | None:
    """Writes the segment to disk and inserts a 'pending' recordings row.
    Returns the new recording id, or None if the segment was too short to keep."""
    duration_sec = len(samples) / sample_rate
    if duration_sec * 1000 < config.vad.min_segment_ms:
        logger.debug("Discarding %.0fms segment (below min_segment_ms)", duration_sec * 1000)
        return None

    day_dir = config.storage.audio_dir / started_at.strftime("%Y/%m/%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    filename = started_at.strftime("%H-%M-%S_%f") + ".flac"
    final_path = day_dir / filename
    tmp_path = final_path.with_suffix(".tmp.flac")

    sf.write(str(tmp_path), samples, sample_rate, format="FLAC")
    tmp_path.rename(final_path)

    relative_path = str(final_path.relative_to(config.storage.data_dir))
    session_id = resolve_session_id(conn, started_at, config.vad.session_gap_minutes)

    cur = conn.execute(
        """INSERT INTO recordings
           (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (
            session_id,
            relative_path,
            started_at.isoformat(),
            ended_at.isoformat(),
            duration_sec,
            sample_rate,
        ),
    )
    conn.commit()
    logger.info(
        "Saved segment %s (%.1fs, session %d)", relative_path, duration_sec, session_id
    )
    return cur.lastrowid
