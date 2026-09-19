import json
from datetime import datetime, timedelta, timezone

import app.summarizer.summarize as summarize_module
from app.config import (
    AudioConfig,
    Config,
    KeywordsConfig,
    LlmBackend,
    StorageConfig,
    SummarizationConfig,
    TranscriptionConfig,
    VadConfig,
    WebConfig,
)
from app.db import connect, init_db
from app.summarizer.summarize import (
    _chunk_lines,
    _split_long_line,
    _validate_result,
    find_missing_summary_days,
    summarize_day,
)
from app.time_utils import utcnow_iso


def _make_config(tmp_path):
    return Config(
        audio=AudioConfig(device=None, sample_rate=16000),
        vad=VadConfig(silence_ms_to_close=1000, min_segment_ms=500, max_segment_seconds=300,
                      session_gap_minutes=5, speech_pad_ms=300, threshold=0.5),
        transcription=TranscriptionConfig(model_size="small.en", compute_type="int8", model_dir=None),
        keywords=KeywordsConfig(triggers=["flag this"]),
        summarization=SummarizationConfig(
            active_backend="local",
            backends={"local": LlmBackend(base_url="http://example.invalid", model="test-model")},
        ),
        storage=StorageConfig(data_dir=tmp_path / "data"),
        web=WebConfig(host="0.0.0.0", port=8420, password_hash="", secret_key="x"),
    )


class FakeLlmClient:
    def __init__(self, base_url, model, timeout=600):
        pass

    def chat(self, messages, temperature=0.3):
        return json.dumps({"summary": "did stuff", "todos": ["buy milk"]})


def test_chunk_lines_single_chunk_when_short():
    lines = ["[09:00] hello", "[09:01] world"]
    assert _chunk_lines(lines, max_chars=1000) == ["\n".join(lines)]


def test_chunk_lines_splits_when_over_limit():
    lines = ["a" * 50 for _ in range(10)]
    chunks = _chunk_lines(lines, max_chars=120)
    assert len(chunks) > 1
    joined = "\n".join(chunks)
    for line in lines:
        assert line in joined


def test_chunk_lines_empty():
    assert _chunk_lines([], max_chars=100) == []


def test_split_long_line_respects_word_boundaries():
    line = "one two three four five six seven eight"
    pieces = _split_long_line(line, max_chars=12)
    assert all(len(p) <= 12 for p in pieces)
    assert " ".join(pieces).split() == line.split()


def test_chunk_lines_splits_a_single_oversized_line():
    # A long uninterrupted recording (no 1.8s+ pause) produces one very long
    # line; it must still get split rather than passed through as one chunk.
    long_line = "[09:00] " + " ".join(f"word{i}" for i in range(200))
    assert len(long_line) > 500

    chunks = _chunk_lines([long_line], max_chars=100)

    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    joined = "\n".join(chunks)
    for i in range(200):
        assert f"word{i}" in joined


def test_validate_result_normalizes_missing_summary():
    assert _validate_result({}) == {"summary": "(model did not return a usable summary)", "todos": []}


def test_validate_result_coerces_and_filters_todos():
    result = _validate_result({"summary": "ok", "todos": ["buy milk", 5, None, {"nested": True}]})
    assert result == {"summary": "ok", "todos": ["buy milk", "5"]}


def test_validate_result_rejects_non_list_todos():
    assert _validate_result({"summary": "ok", "todos": "buy milk"}) == {"summary": "ok", "todos": []}


def _insert_recording_on_local_day(conn, days_ago: int) -> str:
    local_dt = (datetime.now().astimezone() - timedelta(days=days_ago)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    started_at = local_dt.astimezone(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (1, ?, ?, ?, 1.0, 16000, 'transcribed')",
        (f"{days_ago}.flac", started_at, started_at),
    )
    conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, 'hello', '[]', 'test', ?)",
        (cur.lastrowid, utcnow_iso()),
    )
    return local_dt.strftime("%Y-%m-%d")


def test_find_missing_summary_days(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)
    conn = connect(config.storage.db_path)

    day_with_summary = _insert_recording_on_local_day(conn, days_ago=2)
    day_missing = _insert_recording_on_local_day(conn, days_ago=3)
    day_today = _insert_recording_on_local_day(conn, days_ago=0)
    day_too_old = _insert_recording_on_local_day(conn, days_ago=30)
    conn.commit()

    conn.execute(
        "INSERT INTO daily_summaries (day, summary_text, todos_json, model, generated_at) "
        "VALUES (?, 'x', '[]', 'test', ?)",
        (day_with_summary, utcnow_iso()),
    )
    conn.commit()

    missing = find_missing_summary_days(conn, lookback_days=14)

    assert day_missing in missing
    assert day_with_summary not in missing
    assert day_today not in missing  # today isn't finished yet
    assert day_too_old not in missing  # outside the lookback window
    conn.close()


def test_summarize_day_with_mocked_llm(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    today_10am_utc = (
        datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc).isoformat()
    )
    conn = connect(config.storage.db_path)
    cur = conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (1, 'x.flac', ?, ?, 1.0, 16000, 'transcribed')",
        (today_10am_utc, today_10am_utc),
    )
    rec_id = cur.lastrowid
    conn.execute(
        "INSERT INTO transcripts (recording_id, text, words_json, model, created_at) "
        "VALUES (?, 'need to buy milk', '[]', 'test', ?)",
        (rec_id, utcnow_iso()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(summarize_module, "LlmClient", FakeLlmClient)

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    result = summarize_day(config, today)

    assert result == {"summary": "did stuff", "todos": ["buy milk"]}

    conn = connect(config.storage.db_path)
    row = conn.execute("SELECT * FROM daily_summaries WHERE day = ?", (today,)).fetchone()
    conn.close()
    assert row is not None
    assert row["summary_text"] == "did stuff"
    assert json.loads(row["todos_json"]) == ["buy milk"]
