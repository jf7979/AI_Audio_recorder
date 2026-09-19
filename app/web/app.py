"""Flask app factory + waitress-based production entrypoint.

We use waitress instead of the Flask dev server: gunicorn doesn't run on
Windows, and waitress has no debug-mode reloader, so there's no risk of
double-initializing anything on startup.
"""
from __future__ import annotations

import logging

import markupsafe
from flask import Flask, current_app, g, redirect, request, session, url_for

from app.config import Config
from app.db import connect

logger = logging.getLogger("web")


def _highlight(text: str) -> markupsafe.Markup:
    """Escapes text for HTML, then turns our own [[...]] markers (inserted by
    the FTS5 snippet() call) into <mark> tags - keeps transcript text that
    happens to contain '<' or '&' from being interpreted as markup."""
    escaped = str(markupsafe.escape(text))
    return markupsafe.Markup(escaped.replace("[[", "<mark>").replace("]]", "</mark>"))


def get_db():
    if "db" not in g:
        config: Config = current_app.config["APP_CONFIG"]
        g.db = connect(config.storage.db_path)
    return g.db


def _close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_app(config: Config) -> Flask:
    if not config.web.password_hash or not config.web.secret_key:
        raise RuntimeError(
            "No web login is configured (web_auth.yaml is missing password_hash/secret_key). "
            "Run scripts/setup_password.py before starting the web server - it binds to your "
            "LAN, so it must not start with a guessable/fallback session secret."
        )

    app = Flask(__name__)
    app.config["APP_CONFIG"] = config
    app.secret_key = config.web.secret_key
    # SameSite=Lax stops the session cookie being sent on cross-site requests
    # (e.g. a POST from another site), which covers CSRF for this app's
    # state-changing routes (flag toggling, summary generation) without
    # needing per-form tokens. Not Secure=True: this is plain HTTP on a LAN.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    app.teardown_appcontext(_close_db)
    app.jinja_env.filters["highlight"] = _highlight

    from app.web.auth import auth_bp
    from app.web.routes.browse import browse_bp
    from app.web.routes.flags import flags_bp
    from app.web.routes.search import search_bp
    from app.web.routes.summary import summary_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(browse_bp)
    app.register_blueprint(flags_bp)
    app.register_blueprint(summary_bp)

    # Blanket login requirement, except for the login route itself and static assets.
    exempt = {"auth.login", "static"}

    @app.before_request
    def _require_login():
        if request.endpoint in exempt or request.endpoint is None:
            return None
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))
        return None

    return app


def run_app(config: Config) -> None:
    from waitress import serve

    app = create_app(config)
    logger.info("Starting web server on %s:%d", config.web.host, config.web.port)
    serve(app, host=config.web.host, port=config.web.port)
