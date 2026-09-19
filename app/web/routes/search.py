"""Full-text search over transcripts, with click-to-seek into the matched word."""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, render_template, request

from app.web.app import get_db

search_bp = Blueprint("search", __name__)


def _tokens(q: str) -> list[str]:
    return re.findall(r"\w+", q)


def _build_fts_query(tokens: list[str]) -> str:
    return " ".join(f'"{t}"' for t in tokens)


def _find_seek_time(words_json: str | None, tokens: set[str]) -> float:
    if not words_json:
        return 0.0
    try:
        words = json.loads(words_json)
    except json.JSONDecodeError:
        return 0.0
    for w in words:
        clean = re.sub(r"\W", "", w.get("word", "")).lower()
        if clean in tokens:
            return w.get("start", 0.0)
    return 0.0


@search_bp.route("/")
def index():
    return search()


@search_bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    results = []
    tokens = _tokens(q)
    if tokens:
        db = get_db()
        rows = db.execute(
            """SELECT r.id as recording_id, r.session_id, r.started_at, t.words_json,
                      snippet(transcripts_fts, 0, '[[', ']]', '...', 12) as snippet
               FROM transcripts_fts
               JOIN transcripts t ON t.id = transcripts_fts.rowid
               JOIN recordings r ON r.id = t.recording_id
               WHERE transcripts_fts MATCH ?
               ORDER BY bm25(transcripts_fts)
               LIMIT 50""",
            (_build_fts_query(tokens),),
        ).fetchall()
        token_set = {t.lower() for t in tokens}
        for row in rows:
            local_dt = datetime.fromisoformat(row["started_at"]).astimezone()
            results.append(
                {
                    "recording_id": row["recording_id"],
                    "session_id": row["session_id"],
                    "date": local_dt.strftime("%Y-%m-%d"),
                    "time": local_dt.strftime("%H:%M"),
                    "snippet": row["snippet"],
                    "seek": _find_seek_time(row["words_json"], token_set),
                }
            )
    return render_template("search.html", q=q, results=results)
