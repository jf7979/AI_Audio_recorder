"""Shared logging setup for the recorder/transcriber/web/summarizer entrypoints."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(name: str, logs_dir: Path) -> logging.Logger:
    """Configures the ROOT logger for this process (each of the 4 entrypoints
    is a separate OS process, so this only affects that one process's log
    file) - not just this process's own named logger. Otherwise logging from
    dependencies (Flask, Waitress, faster-whisper, etc.) never reaches the
    file, since e.g. "waitress" and "web" are unrelated logger names with no
    parent/child relationship."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = RotatingFileHandler(
        logs_dir / f"{name}.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    return logging.getLogger(name)
