from datetime import datetime, timezone

from app.db import connect, init_db
from app.recorder.storage import renumber_sessions, resolve_session_id


def _insert(conn, session_id, started_at, ended_at, file_path="x.flac"):
    conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (?, ?, ?, ?, 1.0, 16000, 'pending')",
        (session_id, file_path, started_at, ended_at),
    )
    conn.commit()


def test_resolve_session_id_starts_at_one_for_empty_db(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    init_db(db_path)
    conn = connect(db_path)

    assert resolve_session_id(conn, datetime(2026, 1, 1, tzinfo=timezone.utc), gap_minutes=5) == 1
    conn.close()


def test_resolve_session_id_joins_recent_session(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    _insert(conn, 1, "2026-01-01T10:00:00+00:00", "2026-01-01T10:00:05+00:00")

    started_at = datetime(2026, 1, 1, 10, 1, 0, tzinfo=timezone.utc)  # 55s after last ended_at
    assert resolve_session_id(conn, started_at, gap_minutes=5) == 1
    conn.close()


def test_resolve_session_id_starts_new_session_after_gap(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    _insert(conn, 1, "2026-01-01T10:00:00+00:00", "2026-01-01T10:00:05+00:00")

    started_at = datetime(2026, 1, 1, 10, 10, 0, tzinfo=timezone.utc)  # ~10 min later
    assert resolve_session_id(conn, started_at, gap_minutes=5) == 2
    conn.close()


def test_renumber_sessions_groups_by_gap_regardless_of_prior_values(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    # Deliberately wrong/placeholder session_ids, inserted out of
    # chronological order, to prove renumbering derives everything fresh
    # from started_at/gap rather than trusting whatever was there before.
    _insert(conn, 99, "2026-01-01T09:00:00+00:00", "2026-01-01T09:00:01+00:00", "a.flac")
    _insert(conn, 0, "2026-01-01T09:00:30+00:00", "2026-01-01T09:00:31+00:00", "b.flac")
    _insert(conn, 5, "2026-01-01T12:00:00+00:00", "2026-01-01T12:00:01+00:00", "c.flac")

    renumber_sessions(conn, gap_minutes=5)

    rows = {
        row["file_path"]: row["session_id"]
        for row in conn.execute("SELECT file_path, session_id FROM recordings")
    }
    assert rows["a.flac"] == rows["b.flac"] == 1  # 30s apart - same session
    assert rows["c.flac"] == 2  # hours later - new session
    conn.close()
