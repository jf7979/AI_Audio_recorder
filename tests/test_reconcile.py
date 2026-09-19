from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

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
from app.recorder.reconcile import reconcile_orphans


def _make_config(tmp_path):
    return Config(
        audio=AudioConfig(device=None, sample_rate=16000),
        vad=VadConfig(silence_ms_to_close=1800, min_segment_ms=800, max_segment_seconds=300,
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


def _write_flac(path: Path, seconds: float, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.zeros(int(seconds * sample_rate), dtype=np.int16)
    sf.write(str(path), samples, sample_rate, format="FLAC")


def test_reconcile_recovers_orphaned_file(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    orphan_path = config.storage.audio_dir / "2026" / "01" / "15" / "10-30-00_000000.flac"
    _write_flac(orphan_path, seconds=2.0)

    conn = connect(config.storage.db_path)
    recovered = reconcile_orphans(config, conn)

    assert recovered == 1
    row = conn.execute("SELECT * FROM recordings").fetchone()
    assert row["status"] == "pending"
    assert row["file_path"] == str(Path("audio/2026/01/15/10-30-00_000000.flac"))
    assert row["started_at"] == datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc).isoformat()
    assert row["duration_sec"] == pytest.approx(2.0, abs=0.05)
    conn.close()


def test_reconcile_ignores_already_known_files(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    known_path = config.storage.audio_dir / "2026" / "01" / "15" / "10-30-00_000000.flac"
    _write_flac(known_path, seconds=1.0)

    conn = connect(config.storage.db_path)
    conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (1, ?, '2026-01-15T10:30:00+00:00', '2026-01-15T10:30:01+00:00', 1.0, 16000, 'pending')",
        (str(Path("audio/2026/01/15/10-30-00_000000.flac")),),
    )
    conn.commit()

    recovered = reconcile_orphans(config, conn)

    assert recovered == 0
    assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
    conn.close()


def test_reconcile_assigns_session_ids_chronologically(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    # Far enough apart to land in separate sessions (session_gap_minutes=5).
    _write_flac(config.storage.audio_dir / "2026" / "01" / "15" / "10-00-00_000000.flac", seconds=1.0)
    _write_flac(config.storage.audio_dir / "2026" / "01" / "15" / "10-30-00_000000.flac", seconds=1.0)

    conn = connect(config.storage.db_path)
    recovered = reconcile_orphans(config, conn)

    assert recovered == 2
    rows = conn.execute("SELECT session_id FROM recordings ORDER BY started_at ASC").fetchall()
    assert rows[0]["session_id"] != rows[1]["session_id"]
    conn.close()


def test_reconcile_renumbers_when_orphan_precedes_an_existing_later_session(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    conn = connect(config.storage.db_path)
    # A recording already in the DB as session 1 - simulating the live
    # recorder having already run once and recorded normally.
    existing_relpath = str(Path("audio/2026/01/15/14-00-00_000000.flac"))
    conn.execute(
        "INSERT INTO recordings (session_id, file_path, started_at, ended_at, duration_sec, sample_rate, status) "
        "VALUES (1, ?, '2026-01-15T14:00:00+00:00', '2026-01-15T14:00:01+00:00', 1.0, 16000, 'pending')",
        (existing_relpath,),
    )
    conn.commit()

    # An orphan discovered on this restart that chronologically belongs
    # BEFORE that recording - naively comparing against "whatever was
    # inserted last" (session 1) would misnumber this.
    orphan_path = config.storage.audio_dir / "2026" / "01" / "15" / "10-00-00_000000.flac"
    _write_flac(orphan_path, seconds=1.0)

    recovered = reconcile_orphans(config, conn)
    assert recovered == 1

    rows = {
        row["file_path"]: row["session_id"]
        for row in conn.execute("SELECT file_path, session_id FROM recordings")
    }
    orphan_relpath = str(Path("audio/2026/01/15/10-00-00_000000.flac"))

    # The earlier (orphaned) recording must be session 1, and the later
    # pre-existing one renumbered to session 2 - never the other way around.
    assert rows[orphan_relpath] == 1
    assert rows[existing_relpath] == 2
    conn.close()


def test_reconcile_ignores_leftover_temp_writes(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    # What a crash mid-write leaves behind. It must be ignored quietly, not
    # treated as a recoverable orphan (and not re-warned about every pass).
    temp_path = config.storage.audio_dir / "2026" / "01" / "15" / "10-30-00_000000.tmp.flac"
    _write_flac(temp_path, seconds=1.0)

    conn = connect(config.storage.db_path)
    recovered = reconcile_orphans(config, conn)

    assert recovered == 0
    assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 0
    conn.close()


def test_reconcile_skips_unparseable_paths(tmp_path):
    config = _make_config(tmp_path)
    init_db(config.storage.db_path)

    bad_path = config.storage.audio_dir / "not-a-date-dir" / "garbage.flac"
    _write_flac(bad_path, seconds=1.0)

    conn = connect(config.storage.db_path)
    recovered = reconcile_orphans(config, conn)

    assert recovered == 0
    conn.close()
