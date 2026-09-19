CREATE TABLE IF NOT EXISTS recordings (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  duration_sec REAL NOT NULL,
  sample_rate INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'transcribed', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_recordings_session ON recordings(session_id);
CREATE INDEX IF NOT EXISTS idx_recordings_started_at ON recordings(started_at);
CREATE INDEX IF NOT EXISTS idx_recordings_status ON recordings(status);

CREATE TABLE IF NOT EXISTS transcripts (
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL UNIQUE REFERENCES recordings(id),
  text TEXT NOT NULL,
  words_json TEXT NOT NULL,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
  text,
  content='transcripts',
  content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
  INSERT INTO transcripts_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
  INSERT INTO transcripts_fts(transcripts_fts, rowid, text) VALUES('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
  INSERT INTO transcripts_fts(transcripts_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO transcripts_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL REFERENCES recordings(id),
  transcript_id INTEGER NOT NULL REFERENCES transcripts(id),
  keyword TEXT NOT NULL,
  snippet TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  is_done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flags_is_done ON flags(is_done);
CREATE INDEX IF NOT EXISTS idx_flags_occurred_at ON flags(occurred_at);

CREATE TABLE IF NOT EXISTS daily_summaries (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL UNIQUE,
  summary_text TEXT NOT NULL,
  todos_json TEXT NOT NULL,
  model TEXT NOT NULL,
  generated_at TEXT NOT NULL
);
