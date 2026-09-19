"""Daily summary + to-do extraction, with a map-reduce fallback for long days
so a heavy speaking day doesn't blow past the active model's context window.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.db import connect
from app.summarizer.llm_client import LlmClient
from app.time_utils import local_day_bounds_utc

logger = logging.getLogger("summarizer")

# Chosen to comfortably fit small local models' context windows (~4-8k tokens)
# alongside the prompt and response; a day under this is summarized in one call.
DIRECT_CHAR_LIMIT = 6000
CHUNK_CHAR_LIMIT = 4000

MAP_PROMPT = """You are summarizing a portion of someone's day from transcribed \
speech recordings (their own personal voice log - not a conversation with you). \
Write a brief neutral summary of what was discussed or done, and list any action \
items / to-dos mentioned.

Transcript excerpt (timestamps are local time):
{excerpt}

Respond with JSON only, in exactly this shape:
{{"summary": "...", "todos": ["...", "..."]}}"""

REDUCE_PROMPT = """You are combining several partial summaries of someone's day \
(from their personal voice log) into one cohesive daily summary. Merge similar \
to-dos and remove duplicates.

Partial summaries:
{parts}

Respond with JSON only, in exactly this shape:
{{"summary": "...", "todos": ["...", "..."]}}"""


def _fetch_day_rows(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    start, end = local_day_bounds_utc(day)
    return conn.execute(
        """SELECT r.started_at, t.text FROM recordings r
           JOIN transcripts t ON t.recording_id = r.id
           WHERE r.started_at >= ? AND r.started_at < ?
           ORDER BY r.started_at ASC""",
        (start, end),
    ).fetchall()


def _format_line(row: sqlite3.Row) -> str:
    local_time = datetime.fromisoformat(row["started_at"]).astimezone().strftime("%H:%M")
    return f"[{local_time}] {row['text']}"


def _split_long_line(line: str, max_chars: int) -> list[str]:
    """Breaks a single over-long line (one very long uninterrupted recording,
    e.g. a monologue with no 1.8s+ pause) into pieces at word boundaries."""
    words = line.split(" ")
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current and current_len + len(word) + 1 > max_chars:
            pieces.append(" ".join(current))
            current, current_len = [], 0
        current.append(word)
        current_len += len(word) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces


def _chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        for piece in _split_long_line(line, max_chars) if len(line) > max_chars else [line]:
            if current and current_len + len(piece) > max_chars:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            current.append(piece)
            current_len += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _validate_result(result: dict) -> dict:
    """Normalizes a parsed LLM response so a malformed-but-parseable reply
    (wrong key names, todos as a string instead of a list, etc.) degrades
    gracefully instead of raising a KeyError deep in a batch/nightly job."""
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = "(model did not return a usable summary)"
    todos_raw = result.get("todos")
    todos = [str(t) for t in todos_raw if isinstance(t, (str, int, float))] if isinstance(todos_raw, list) else []
    return {"summary": summary, "todos": todos}


def _ask_json(client: LlmClient, prompt: str) -> dict:
    content = client.chat([{"role": "user", "content": prompt}])
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            raise
        parsed = json.loads(content[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object from the model, got: {parsed!r}")
    return _validate_result(parsed)


def find_missing_summary_days(conn: sqlite3.Connection, lookback_days: int = 14) -> list[str]:
    """Local-calendar days (within the last lookback_days) that have
    recordings but no daily_summaries row yet, oldest first. Never includes
    today, which isn't finished. Used so one failed nightly run doesn't
    create a permanent gap - the next successful run catches up on it too."""
    cutoff = (datetime.now().astimezone() - timedelta(days=lookback_days)).astimezone(timezone.utc).isoformat()
    rows = conn.execute("SELECT started_at FROM recordings WHERE started_at >= ?", (cutoff,)).fetchall()
    candidate_days = {
        datetime.fromisoformat(r["started_at"]).astimezone().strftime("%Y-%m-%d") for r in rows
    }
    existing_days = {r["day"] for r in conn.execute("SELECT day FROM daily_summaries").fetchall()}
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return sorted(candidate_days - existing_days - {today})


def summarize_day(config: Config, day: str, backend_name: str | None = None) -> dict:
    """Generates (or regenerates) the summary for `day` (YYYY-MM-DD, local time)
    and upserts it into daily_summaries. Returns {"summary": str, "todos": [str]}."""
    backend = config.summarization.resolve(backend_name)
    client = LlmClient(backend.base_url, backend.model)

    conn = connect(config.storage.db_path)
    try:
        rows = _fetch_day_rows(conn, day)
        if not rows:
            result = {"summary": "No recordings for this day.", "todos": []}
        else:
            lines = [_format_line(r) for r in rows]
            full_text = "\n".join(lines)

            if len(full_text) <= DIRECT_CHAR_LIMIT:
                result = _ask_json(client, MAP_PROMPT.format(excerpt=full_text))
            else:
                chunks = _chunk_lines(lines, CHUNK_CHAR_LIMIT)
                logger.info("Day %s: %d chars, mapping over %d chunks", day, len(full_text), len(chunks))
                parts = [_ask_json(client, MAP_PROMPT.format(excerpt=c)) for c in chunks]
                parts_text = "\n\n".join(
                    f"Summary: {p.get('summary', '')}\nTodos: {', '.join(p.get('todos', [])) or 'none'}"
                    for p in parts
                )
                result = _ask_json(client, REDUCE_PROMPT.format(parts=parts_text))

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO daily_summaries (day, summary_text, todos_json, model, generated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                 summary_text = excluded.summary_text,
                 todos_json = excluded.todos_json,
                 model = excluded.model,
                 generated_at = excluded.generated_at""",
            (day, result["summary"], json.dumps(result.get("todos", [])), backend.model, now),
        )
        conn.commit()
        logger.info("Summarized %s using %s", day, backend.model)
        return result
    finally:
        conn.close()
