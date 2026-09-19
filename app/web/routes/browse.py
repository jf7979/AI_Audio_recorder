"""Browsing recordings by day -> session (conversation) -> individual clips."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, abort, current_app, render_template, send_file

from app.time_utils import local_day_bounds_utc
from app.web.app import get_db

browse_bp = Blueprint("browse", __name__)


@browse_bp.route("/days")
def days():
    db = get_db()
    rows = db.execute("SELECT started_at FROM recordings ORDER BY started_at ASC").fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        local_date = datetime.fromisoformat(row["started_at"]).astimezone().strftime("%Y-%m-%d")
        counts[local_date] = counts.get(local_date, 0) + 1

    summarized = {r["day"] for r in db.execute("SELECT day FROM daily_summaries").fetchall()}
    days_list = [
        {"date": date, "count": count, "has_summary": date in summarized}
        for date, count in sorted(counts.items(), reverse=True)
    ]
    return render_template("days.html", days=days_list)


@browse_bp.route("/day/<date>")
def day_view(date):
    db = get_db()
    start, end = local_day_bounds_utc(date)
    rows = db.execute(
        "SELECT * FROM recordings WHERE started_at >= ? AND started_at < ? ORDER BY started_at ASC",
        (start, end),
    ).fetchall()

    sessions: dict[int, list] = {}
    for row in rows:
        sessions.setdefault(row["session_id"], []).append(row)

    session_summaries = [
        {
            "session_id": sid,
            "start": datetime.fromisoformat(recs[0]["started_at"]).astimezone().strftime("%H:%M"),
            "end": datetime.fromisoformat(recs[-1]["ended_at"]).astimezone().strftime("%H:%M"),
            "count": len(recs),
        }
        for sid, recs in sorted(sessions.items())
    ]
    summary = db.execute("SELECT * FROM daily_summaries WHERE day = ?", (date,)).fetchone()
    return render_template("day.html", date=date, sessions=session_summaries, summary=summary)


@browse_bp.route("/day/<date>/session/<int:session_id>")
def session_view(date, session_id):
    db = get_db()
    rows = db.execute(
        """SELECT r.id, r.started_at, r.status, t.text, t.words_json
           FROM recordings r
           LEFT JOIN transcripts t ON t.recording_id = r.id
           WHERE r.session_id = ?
           ORDER BY r.started_at ASC""",
        (session_id,),
    ).fetchall()
    if not rows:
        abort(404)

    recordings = []
    for row in rows:
        recordings.append(
            {
                "id": row["id"],
                "local_time": datetime.fromisoformat(row["started_at"]).astimezone().strftime("%H:%M:%S"),
                "text": row["text"],
                "status": row["status"],
                "words": json.loads(row["words_json"]) if row["words_json"] else [],
            }
        )
    return render_template("session.html", date=date, session_id=session_id, recordings=recordings)


@browse_bp.route("/audio/<int:recording_id>")
def audio(recording_id):
    db = get_db()
    row = db.execute("SELECT file_path FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if row is None:
        abort(404)
    config = current_app.config["APP_CONFIG"]
    path = config.storage.data_dir / row["file_path"]
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="audio/flac", conditional=True)
