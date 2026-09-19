"""Recovers audio files that exist on disk but have no recordings row.

finalize_segment() writes the FLAC file (atomically, via rename) before
inserting its DB row, so that a crash never leaves a half-written audio file
referenced by the database. The flip side is that a crash in the narrow
window between the rename and the INSERT leaves an orphaned file the app
doesn't know about. Rather than reordering those two steps - which would
instead risk losing the recorded audio itself if the crash happened before
the file was durably written, a worse trade-off for a tool whose whole point
is not losing what was recorded - we self-heal by scanning for orphans once
at startup. NSSM restarts this process after any crash, so this naturally
runs right when it'd be needed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from app.config import Config
from app.recorder.storage import renumber_sessions

logger = logging.getLogger("recorder")


def _started_at_from_path(audio_dir: Path, path: Path) -> datetime | None:
    """Recovers the UTC start time encoded in <audio_dir>/YYYY/MM/DD/HH-MM-SS_ffffff.flac."""
    try:
        year, month, day = path.relative_to(audio_dir).parts[:3]
        hms, micros = path.stem.split("_")
        hour, minute, second = hms.split("-")
        return datetime(
            int(year), int(month), int(day),
            int(hour), int(minute), int(second), int(micros),
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return None


def reconcile_orphans(config: Config, conn: sqlite3.Connection) -> int:
    """Finds .flac files under audio_dir with no matching recordings row,
    and inserts one (status='pending') for each. Returns the number recovered.

    Orphans are inserted with a placeholder session_id=0, then ALL recordings'
    session_id values are recomputed from scratch in chronological order
    (renumber_sessions) - an orphan found via a directory scan has no
    guaranteed relationship to whatever the highest existing row id is, so a
    locally-computed session number for it can't be trusted (see
    resolve_session_id's docstring)."""
    known_paths = {row["file_path"] for row in conn.execute("SELECT file_path FROM recordings")}

    orphans = []
    stale_temp_files = 0
    for path in config.storage.audio_dir.rglob("*.flac"):
        # finalize_segment writes to "<name>.tmp.flac" and renames on success,
        # so a crash mid-write can leave one behind. Those are partial/unusable
        # and would otherwise fail to parse and warn on every single pass.
        if path.name.endswith(".tmp.flac"):
            stale_temp_files += 1
            continue
        relative_path = str(path.relative_to(config.storage.data_dir))
        if relative_path in known_paths:
            continue
        started_at = _started_at_from_path(config.storage.audio_dir, path)
        if started_at is None:
            logger.warning("Skipping orphan %s: couldn't parse a timestamp from its path", path)
            continue
        orphans.append((started_at, relative_path, path))

    orphans.sort(key=lambda item: item[0])

    recovered = 0
    for started_at, relative_path, path in orphans:
        try:
            info = sf.info(str(path))
        except Exception:
            logger.exception("Skipping orphan %s: couldn't read it as audio", path)
            continue

        ended_at_ts = started_at.timestamp() + info.duration
        ended_at = datetime.fromtimestamp(ended_at_ts, tz=timezone.utc)

        conn.execute(
            """INSERT INTO recordings
               (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status)
               VALUES (0, ?, ?, ?, ?, ?, 'pending')""",
            (relative_path, started_at.isoformat(), ended_at.isoformat(),
             info.duration, info.samplerate),
        )
        conn.commit()
        recovered += 1
        logger.warning("Recovered orphaned recording %s", relative_path)

    if stale_temp_files:
        logger.info(
            "Ignoring %d leftover *.tmp.flac partial write(s) under %s - safe to delete",
            stale_temp_files, config.storage.audio_dir,
        )

    if recovered:
        renumber_sessions(conn, config.vad.session_gap_minutes)
        logger.warning("Reconciliation recovered %d orphaned recording(s)", recovered)
    return recovered
