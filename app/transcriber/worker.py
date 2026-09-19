"""Polls for pending recordings, transcribes them, and scans for flags."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from app.config import Config
from app.db import connect
from app.time_utils import utcnow_iso
from app.transcriber.engine import TranscriptionEngine
from app.transcriber.keywords import find_flags

logger = logging.getLogger("transcriber")

POLL_INTERVAL_SEC = 2
BATCH_SIZE = 10
# A newly-created file getting briefly locked by antivirus/indexing on Windows
# is a real, plausible transient failure on the target hardware - retry a
# couple of times (each retry naturally spaced out by the poll loop) before
# giving up for good.
MAX_TRANSCRIBE_ATTEMPTS = 3


def _store_transcript(
    config: Config, conn: sqlite3.Connection, row: sqlite3.Row, text: str, words: list[dict]
) -> None:
    now = utcnow_iso()
    cur = conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (row["id"], text, json.dumps(words), config.transcription.model_size, now),
    )
    transcript_id = cur.lastrowid
    conn.execute("UPDATE recordings SET status='transcribed' WHERE id=?", (row["id"],))

    for keyword, snippet in find_flags(text, config.keywords.triggers):
        conn.execute(
            "INSERT INTO flags (recording_id, transcript_id, keyword, snippet, occurred_at, is_done, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (row["id"], transcript_id, keyword, snippet, row["started_at"], now),
        )
        logger.info("Flagged (%s): %s", keyword, snippet[:80])

    conn.commit()


def process_one(
    config: Config,
    conn: sqlite3.Connection,
    engine: TranscriptionEngine,
    row: sqlite3.Row,
    attempt_counts: dict[int, int],
) -> None:
    audio_path = config.storage.data_dir / row["file_path"]

    # Both the transcription AND the database writes are covered here: a
    # transient "database is locked" (the recorder writes from another
    # process) must not escape and kill the whole worker, which would stop
    # transcription for every other recording too.
    try:
        text, words = engine.transcribe(audio_path)
        _store_transcript(config, conn, row, text, words)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.exception("Rollback after failed transcription also failed")

        attempts = attempt_counts.get(row["id"], 0) + 1
        if attempts < MAX_TRANSCRIBE_ATTEMPTS:
            attempt_counts[row["id"]] = attempts
            logger.warning(
                "Attempt %d/%d failed for recording %d (%s); will retry",
                attempts, MAX_TRANSCRIBE_ATTEMPTS, row["id"], audio_path, exc_info=True,
            )
            return

        logger.exception(
            "Giving up on recording %d (%s) after %d attempts", row["id"], audio_path, attempts
        )
        attempt_counts.pop(row["id"], None)
        try:
            conn.execute("UPDATE recordings SET status='error' WHERE id=?", (row["id"],))
            conn.commit()
        except Exception:
            # Even marking it failed didn't work - leave it 'pending' and let
            # a later poll (or restart) deal with it rather than crashing.
            logger.exception("Could not mark recording %d as errored", row["id"])
        return

    attempt_counts.pop(row["id"], None)
    logger.info("Transcribed recording %d (%d chars)", row["id"], len(text))


def run_forever(config: Config) -> None:
    engine = TranscriptionEngine(
        config.transcription.model_size, config.transcription.compute_type, config.transcription.model_dir
    )
    logger.info("Transcriber ready (model=%s, compute_type=%s)",
                config.transcription.model_size, config.transcription.compute_type)
    attempt_counts: dict[int, int] = {}

    while True:
        # This loop must never exit on a transient error - the transcriber is
        # the only thing draining the pending queue, so crashing out of here
        # stalls everything until a service restart.
        try:
            conn = connect(config.storage.db_path)
            try:
                rows = conn.execute(
                    "SELECT * FROM recordings WHERE status='pending' ORDER BY id ASC LIMIT ?",
                    (BATCH_SIZE,),
                ).fetchall()
                for row in rows:
                    process_one(config, conn, engine, row, attempt_counts)
            finally:
                conn.close()
        except Exception:
            logger.exception("Transcriber poll cycle failed; retrying after the usual interval")

        # Always pace the loop (rather than only sleeping when idle) so a
        # persistently-failing recording left in 'pending' for a retry can't
        # spin the loop tightly.
        time.sleep(POLL_INTERVAL_SEC)
