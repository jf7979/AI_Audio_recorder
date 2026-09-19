#!/usr/bin/env python3
"""Entrypoint: python -m app.transcriber.main"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import ensure_data_dirs, load_config
from app.db import init_db
from app.logging_setup import setup_logging
from app.transcriber.worker import run_forever


def main() -> None:
    config = load_config()
    ensure_data_dirs(config)
    logger = setup_logging("transcriber", config.storage.logs_dir)
    init_db(config.storage.db_path)
    try:
        run_forever(config)
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
