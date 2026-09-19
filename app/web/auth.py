"""Single-password session login. Good enough for a personal LAN tool that
holds recordings of your own voice - not meant to be exposed to the internet."""
from __future__ import annotations

import time
from collections import defaultdict

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

auth_bp = Blueprint("auth", __name__)

# In-memory, per-process rate limiting on failed logins. Fine for a
# single-process personal tool; resets on restart, which is an acceptable
# trade-off against the complexity of a persistent store for this threat model.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _is_safe_redirect_target(target: str) -> bool:
    # Must be a same-site relative path, not "//host/..." (protocol-relative)
    # or an absolute URL - otherwise ?next= could be used to redirect a
    # freshly-logged-in user to an attacker's site.
    return target.startswith("/") and not target.startswith("//")


def _is_rate_limited(key: str) -> bool:
    cutoff = time.time() - LOCKOUT_WINDOW_SECONDS
    recent = [t for t in _failed_attempts[key] if t > cutoff]
    _failed_attempts[key] = recent
    return len(recent) >= MAX_FAILED_ATTEMPTS


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    config = current_app.config["APP_CONFIG"]
    error = None
    if request.method == "POST":
        client_key = request.remote_addr or "unknown"
        if _is_rate_limited(client_key):
            error = "Too many failed attempts. Try again in a few minutes."
        else:
            password = request.form.get("password", "")
            if not config.web.password_hash:
                error = "No password is configured yet - run scripts/setup_password.py."
            elif check_password_hash(config.web.password_hash, password):
                _failed_attempts.pop(client_key, None)
                session.clear()
                session["logged_in"] = True
                session.permanent = True
                next_url = request.args.get("next", "")
                destination = next_url if _is_safe_redirect_target(next_url) else url_for("search.index")
                return redirect(destination)
            else:
                _failed_attempts[client_key].append(time.time())
                error = "Incorrect password."
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
