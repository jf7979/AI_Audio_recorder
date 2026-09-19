# AI Audio Recorder

A local, always-listening voice transcription and life-log system: a USB mic
feeds a voice-activity-triggered recorder, speech gets transcribed with
word-level timestamps by `faster-whisper`, everything is searchable and
browsable in a small web UI, and a local/LAN LLM (Ollama or LM Studio)
generates daily summaries and to-do extraction. Saying a configured trigger
phrase (e.g. "flag this") captures whatever you say next as a flagged item.

Fully local - no cloud services. Designed to run on a low-power mini PC
(developed against an Intel N100, 16GB RAM, Windows 11).

See [SETUP.md](SETUP.md) for installation and deployment instructions
(Windows services via NSSM, nightly summaries via Task Scheduler).

## Layout

- `app/recorder/` - mic capture, Silero VAD segmentation, FLAC storage
- `app/transcriber/` - faster-whisper transcription + keyword flagging
- `app/summarizer/` - daily summary / to-do extraction via a local LLM
- `app/web/` - Flask UI: search, browse by day/session, flags, summaries
- `scripts/` - one-off setup commands (`init_db.py`, `setup_password.py`) and
  a dev convenience launcher (`run_all.py`)
- `tests/` - hardware-independent tests (`pip install -r requirements-dev.txt && pytest`)

## Config

Copy `config.example.yaml` to `config.yaml` and edit it for your machine -
see the comments in that file and [SETUP.md](SETUP.md) for details. The web
login password lives in a separate, machine-generated `web_auth.yaml`
(created by `scripts/setup_password.py`), not in `config.yaml`.
