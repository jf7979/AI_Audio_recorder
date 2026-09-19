#!/usr/bin/env python3
"""Creates the SQLite database and applies schema.sql. Safe to re-run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ensure_data_dirs, load_config
from app.db import init_db


def main() -> None:
    config = load_config()
    ensure_data_dirs(config)
    init_db(config.storage.db_path)
    print(f"Database ready at {config.storage.db_path}")


if __name__ == "__main__":
    main()
