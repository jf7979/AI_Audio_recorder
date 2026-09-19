from app.db import connect, fts_integrity_check, init_db
from app.time_utils import utcnow_iso


def _make_db(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    init_db(db_path)
    return db_path


def _insert_recording(conn, started_at="2026-01-01T00:00:00+00:00"):
    cur = conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (1, 'x.flac', ?, ?, 1.0, 16000, 'pending')",
        (started_at, started_at),
    )
    conn.commit()
    return cur.lastrowid


def test_fts_search_after_insert(tmp_path):
    conn = connect(_make_db(tmp_path))
    rec_id = _insert_recording(conn)
    conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, ?, '[]', 'test', ?)",
        (rec_id, "please remember to buy milk tomorrow", utcnow_iso()),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT t.text FROM transcripts_fts JOIN transcripts t ON t.id = transcripts_fts.rowid "
        "WHERE transcripts_fts MATCH 'milk'"
    ).fetchall()
    assert len(rows) == 1
    assert "milk" in rows[0]["text"]
    conn.close()


def test_fts_search_after_update(tmp_path):
    conn = connect(_make_db(tmp_path))
    rec_id = _insert_recording(conn)
    cur = conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, ?, '[]', 'test', ?)",
        (rec_id, "original text about apples", utcnow_iso()),
    )
    transcript_id = cur.lastrowid
    conn.commit()

    conn.execute("UPDATE transcripts SET text = ? WHERE id = ?", ("updated text about oranges", transcript_id))
    conn.commit()

    assert conn.execute("SELECT 1 FROM transcripts_fts WHERE transcripts_fts MATCH 'apples'").fetchall() == []
    assert len(conn.execute("SELECT 1 FROM transcripts_fts WHERE transcripts_fts MATCH 'oranges'").fetchall()) == 1
    conn.close()


def test_fts_search_after_delete(tmp_path):
    conn = connect(_make_db(tmp_path))
    rec_id = _insert_recording(conn)
    cur = conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, ?, '[]', 'test', ?)",
        (rec_id, "some unique bananaword here", utcnow_iso()),
    )
    transcript_id = cur.lastrowid
    conn.commit()

    conn.execute("DELETE FROM transcripts WHERE id = ?", (transcript_id,))
    conn.commit()

    assert conn.execute("SELECT 1 FROM transcripts_fts WHERE transcripts_fts MATCH 'bananaword'").fetchall() == []
    conn.close()


def test_fts_integrity_check_clean_db(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect(db_path)
    rec_id = _insert_recording(conn)
    conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, ?, '[]', 'test', ?)",
        (rec_id, "integrity check sample text", utcnow_iso()),
    )
    conn.commit()
    conn.close()

    assert fts_integrity_check(db_path) is True
