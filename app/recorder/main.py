#!/usr/bin/env python3
"""Entrypoint: python -m app.recorder.main"""
from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import Config, ensure_data_dirs, load_config
from app.db import connect, init_db
from app.logging_setup import setup_logging
from app.recorder.capture import AudioCapture
from app.recorder.reconcile import reconcile_orphans
from app.recorder.segmenter import Segmenter

# How often to re-scan for orphaned audio files (see reconcile.py) beyond the
# one-time startup pass, so a crash-orphan doesn't sit undiscovered for as
# long as this process happens to stay up without restarting.
RECONCILE_INTERVAL_SECONDS = 3600.0


def _raise_keyboard_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt()


def _install_graceful_shutdown_handlers() -> None:
    # SIGTERM (POSIX) and SIGBREAK (Windows CTRL_BREAK_EVENT) both default to
    # an immediate, uncatchable-by-our-code exit - route them through the
    # same KeyboardInterrupt path as Ctrl+C so an in-progress segment still
    # gets finalized (see Segmenter.close()) instead of silently dropped.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)


def _reconciliation_loop(config: Config, stop_event: threading.Event) -> None:
    while not stop_event.wait(RECONCILE_INTERVAL_SECONDS):
        conn = connect(config.storage.db_path)
        try:
            reconcile_orphans(config, conn)
        except Exception:
            logging.getLogger("recorder").exception("Periodic orphan reconciliation failed")
        finally:
            conn.close()


def main() -> None:
    _install_graceful_shutdown_handlers()

    config = load_config()
    ensure_data_dirs(config)
    logger = setup_logging("recorder", config.storage.logs_dir)
    init_db(config.storage.db_path)

    conn = connect(config.storage.db_path)
    reconcile_orphans(config, conn)

    capture = AudioCapture(sample_rate=config.audio.sample_rate, device=config.audio.device)
    segmenter = Segmenter(config, conn)

    reconcile_stop = threading.Event()
    reconcile_thread = threading.Thread(
        target=_reconciliation_loop, args=(config, reconcile_stop), daemon=True
    )
    reconcile_thread.start()

    capture.start()
    try:
        segmenter.run_forever(capture)
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    finally:
        reconcile_stop.set()
        capture.stop()
        segmenter.close()
        conn.close()


if __name__ == "__main__":
    main()
