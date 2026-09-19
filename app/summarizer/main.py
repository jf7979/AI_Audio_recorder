#!/usr/bin/env python3
"""Entrypoint: python -m app.summarizer.main [--date YYYY-MM-DD] [--backend local|remote]

Intended to be triggered nightly by Windows Task Scheduler (see SETUP.md).
With no --date, processes yesterday PLUS any other recent day that has
recordings but no summary yet - so if one night's run fails (network blip,
Ollama not ready, etc.) the next successful run backfills it automatically,
rather than leaving a permanent gap. --date processes exactly that one day
(this is what the web UI's "regenerate" button uses, via summarize_day()
directly rather than shelling out to this script).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import ensure_data_dirs, load_config
from app.db import connect, init_db
from app.logging_setup import setup_logging
from app.summarizer.summarize import find_missing_summary_days, summarize_day


def _days_to_process(config, explicit_date: str | None) -> list[str]:
    # An explicit --date always regenerates, even if a summary already exists.
    if explicit_date:
        return [explicit_date]

    yesterday = (datetime.now().astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = connect(config.storage.db_path)
    try:
        missing = find_missing_summary_days(conn)
        already_summarized = {
            row["day"] for row in conn.execute("SELECT day FROM daily_summaries")
        }
    finally:
        conn.close()

    # Never redo a day that already has a summary: it wastes minutes of CPU on
    # this hardware, and would clobber a summary deliberately regenerated
    # against the more capable remote backend.
    return sorted(set(missing) | ({yesterday} - already_summarized))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (local time). Defaults to yesterday + any recent gap.")
    parser.add_argument("--backend", help="Named backend from config.yaml. Defaults to active_backend.")
    args = parser.parse_args()

    config = load_config()
    ensure_data_dirs(config)
    logger = setup_logging("summarizer", config.storage.logs_dir)
    init_db(config.storage.db_path)

    days = _days_to_process(config, args.date)
    backend_label = args.backend or config.summarization.active_backend
    if not days:
        logger.info("Nothing to summarize - every recent day already has a summary")
        return

    failures = 0
    for day in days:
        logger.info("Summarizing %s (backend=%s)", day, backend_label)
        try:
            result = summarize_day(config, day, backend_name=args.backend)
        except Exception:
            failures += 1
            logger.exception("Failed to summarize %s - will retry on the next run", day)
            continue
        print(f"=== {day} ===")
        print(result["summary"])
        for todo in result.get("todos", []):
            print(f"- {todo}")

    # Exit non-zero so Task Scheduler's "restart on failure" actually fires
    # (see SETUP.md) - the per-day `continue` above means one bad day doesn't
    # stop the others, but the run as a whole still has to report failure.
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
