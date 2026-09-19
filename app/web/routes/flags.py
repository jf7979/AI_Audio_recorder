"""Flagged / to-do items captured via a spoken trigger phrase (e.g. "flag this")."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from app.web.app import get_db

flags_bp = Blueprint("flags", __name__)

_WHERE_BY_STATUS = {
    "open": "WHERE is_done = 0",
    "done": "WHERE is_done = 1",
    "all": "",
}


@flags_bp.route("/flags")
def index():
    status = request.args.get("status", "open")
    if status not in _WHERE_BY_STATUS:
        status = "open"
    db = get_db()
    rows = db.execute(
        f"""SELECT f.*, r.session_id FROM flags f
            JOIN recordings r ON r.id = f.recording_id
            {_WHERE_BY_STATUS[status]}
            ORDER BY f.occurred_at DESC"""
    ).fetchall()

    flags = []
    for row in rows:
        local_dt = datetime.fromisoformat(row["occurred_at"]).astimezone()
        flags.append(
            {
                "id": row["id"],
                "keyword": row["keyword"],
                "snippet": row["snippet"],
                "is_done": bool(row["is_done"]),
                "date": local_dt.strftime("%Y-%m-%d"),
                "time": local_dt.strftime("%H:%M"),
                "session_id": row["session_id"],
                "recording_id": row["recording_id"],
            }
        )
    return render_template("flags.html", flags=flags, status=status)


@flags_bp.route("/flags/<int:flag_id>/toggle", methods=["POST"])
def toggle(flag_id):
    db = get_db()
    row = db.execute("SELECT is_done FROM flags WHERE id = ?", (flag_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    new_value = 0 if row["is_done"] else 1
    db.execute("UPDATE flags SET is_done = ? WHERE id = ?", (new_value, flag_id))
    db.commit()
    return jsonify({"ok": True, "is_done": bool(new_value)})
