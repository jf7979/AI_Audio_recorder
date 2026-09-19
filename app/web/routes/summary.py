"""Daily summary view + on-demand (re)generation."""
from __future__ import annotations

import json

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from app.summarizer.summarize import summarize_day
from app.web.app import get_db

summary_bp = Blueprint("summary", __name__)


@summary_bp.route("/summary/<date>")
def view(date):
    db = get_db()
    row = db.execute("SELECT * FROM daily_summaries WHERE day = ?", (date,)).fetchone()
    config = current_app.config["APP_CONFIG"]

    summary = None
    if row is not None:
        summary = {
            "text": row["summary_text"],
            "todos": json.loads(row["todos_json"]),
            "model": row["model"],
            "generated_at": row["generated_at"],
        }
    return render_template(
        "summary.html",
        date=date,
        summary=summary,
        backends=sorted(config.summarization.backends),
        active_backend=config.summarization.active_backend,
    )


@summary_bp.route("/summary/<date>/generate", methods=["POST"])
def generate(date):
    config = current_app.config["APP_CONFIG"]
    backend = request.form.get("backend") or None
    # Synchronous on purpose: this is a personal, on-demand button, and a local
    # CPU/LAN model can take anywhere from seconds to a few minutes. A
    # background job queue would be overkill for a single-user tool.
    summarize_day(config, date, backend_name=backend)
    return redirect(url_for("summary.view", date=date))
