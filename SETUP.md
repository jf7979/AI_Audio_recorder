# Setup (Windows 11 mini PC)

## 0. Move the code off any cloud-synced folder

Copy/clone this repo to a plain local folder on the mini PC, e.g. `C:\AudioRecorder`.
Don't run it from a OneDrive/iCloud/Dropbox-synced path - `config.py` will refuse
to start if `storage.data_dir` looks like one, since cloud-sync clients corrupt
SQLite's WAL files.

## 1. Install Python

Install Python 3.11 or 3.12 from python.org (check "Add to PATH" during install).

## 2. Create a virtual environment and install dependencies

```powershell
cd C:\AudioRecorder
python -m venv venv
venv\Scripts\activate

# CPU-only torch first - the default PyPI wheel pulls a multi-GB CUDA build
# that's useless on the N100's integrated GPU:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt
```

## 3. Configure

```powershell
copy config.example.yaml config.yaml
```

Edit `config.yaml`:
- `storage.data_dir`: an absolute local path, e.g. `D:/AudioRecorderData` (or
  `C:/AudioRecorderData` if there's no second drive). This holds all audio +
  the database - make sure it's on a drive with room to grow.
- `audio.device`: run `python -m app.recorder.list_devices` with your USB mic
  plugged in, find its name, and put a distinctive substring here (e.g. `"USB
  Microphone"`). Leave `null` to use the system default input device.
- `summarization.backends.remote.base_url`: the Mac Studio's LAN IP and LM
  Studio's port (default 1234), and the model name exactly as loaded there.
- `transcription.model_dir`: set this to an explicit path (e.g.
  `D:/AudioRecorderData/models`) rather than leaving it `null` if you're going
  to run this as a Windows service (step 6) - see why there, under "service
  account".

Then set your web login password (also generates a session secret key):

```powershell
python scripts/setup_password.py
```

This writes `web_auth.yaml` next to `config.yaml` - it's gitignored and
separate from `config.yaml` on purpose, so re-running it never clobbers your
hand-edited settings or their comments.

Initialize the database:

```powershell
python scripts/init_db.py
```

## 4. Install Ollama (for on-device summaries)

Install from ollama.com, then pull the default model (or whatever you set as
`summarization.backends.local.model` in config.yaml):

```powershell
ollama pull qwen2.5:7b-instruct
```

Ollama runs its own background service automatically after install - nothing
else to start. If your Mac Studio's LM Studio server should be reachable too,
make sure its "local server" is started there and listening on your LAN
interface (not just localhost).

## 5. Try it manually first

```powershell
python scripts\run_all.py
```

This starts the recorder, transcriber, and web server together (Ctrl+C stops
all three). Speak near the mic, wait a couple seconds after you stop talking,
then open `http://localhost:8420` (or `http://<mini-pc-ip>:8420` from another
device on your LAN) and confirm:
- the clip shows up under Days -> today -> a session, with a transcript
- searching for a word you said finds it
- saying one of your `keywords.triggers` phrases (e.g. "flag this") followed
  by something creates an entry under Flags

If Windows Firewall prompts when the web server first starts, allow access on
your **private** network only.

## 6. Run it for real: Windows services via NSSM

Manual `run_all.py` only lasts until you close the terminal or log out. For
a real always-on setup, register the recorder, transcriber, and web server as
three separate Windows services with [NSSM](https://nssm.cc/) - each restarts
independently if it crashes, and all three survive reboots without a login
session.

### Service account (important - don't skip this)

NSSM services run as `LocalSystem` by default, which is a *different account*
than the one you used for the manual test in step 5, and that difference
causes real problems here specifically:
- **Microphone access**: Windows' microphone privacy setting only grants
  access to apps running in a normal user session. A `LocalSystem` service is
  not that, and it also can't answer an interactive "Allow this app to access
  your microphone?" prompt at all (services run in a non-interactive session)
  - it would just hang or silently get no audio.
- **Model cache**: faster-whisper downloads its model into whatever account's
  profile it runs under. `LocalSystem` has no normal user profile, so it may
  fail to download the model or redownload it needlessly even though your
  manual test already worked.

Fix both by creating a **dedicated regular Windows user account** for these
services (e.g. `AudioRecorderSvc`) rather than using `LocalSystem`:
1. Create the account (Settings -> Accounts -> Other users), log into it once.
2. Settings -> Privacy & security -> Microphone -> enable "Let apps access
   your microphone" and confirm your specific mic isn't blocked, while logged
   into that account.
3. Set `transcription.model_dir` in `config.yaml` (see step 3) to an explicit
   path, then pre-download the model once under that same account so the
   service never needs to touch the network for it:
   ```powershell
   venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('small.en', download_root='D:/AudioRecorderData/models')"
   ```
4. When installing each NSSM service below, also run:
   ```powershell
   nssm set <ServiceName> ObjectName ".\AudioRecorderSvc" "<that account's password>"
   ```

Download NSSM, then for each of the three (adjust paths for your install -
the log paths must match whatever `storage.data_dir` you actually set):

```powershell
nssm install VoiceLogRecorder    C:\AudioRecorder\venv\Scripts\python.exe -m app.recorder.main
nssm set VoiceLogRecorder AppDirectory C:\AudioRecorder
nssm set VoiceLogRecorder AppStdout D:\AudioRecorderData\logs\recorder-service.log
nssm set VoiceLogRecorder AppStderr D:\AudioRecorderData\logs\recorder-service.log
nssm set VoiceLogRecorder Start SERVICE_AUTO_START

nssm install VoiceLogTranscriber C:\AudioRecorder\venv\Scripts\python.exe -m app.transcriber.main
nssm set VoiceLogTranscriber AppDirectory C:\AudioRecorder
nssm set VoiceLogTranscriber AppStdout D:\AudioRecorderData\logs\transcriber-service.log
nssm set VoiceLogTranscriber AppStderr D:\AudioRecorderData\logs\transcriber-service.log
nssm set VoiceLogTranscriber Start SERVICE_AUTO_START

nssm install VoiceLogWeb         C:\AudioRecorder\venv\Scripts\python.exe -m app.web.main
nssm set VoiceLogWeb AppDirectory C:\AudioRecorder
nssm set VoiceLogWeb AppStdout D:\AudioRecorderData\logs\web-service.log
nssm set VoiceLogWeb AppStderr D:\AudioRecorderData\logs\web-service.log
nssm set VoiceLogWeb Start SERVICE_AUTO_START
```

The `AppStdout`/`AppStderr` redirects are a safety net for anything that
happens before logging is even set up (or a truly unhandled crash) - normal
operation, including from Flask/Waitress internals, already lands in each
module's own rotating log under `<data_dir>/logs/<name>.log`.

Then start them (or just reboot):

```powershell
nssm start VoiceLogRecorder
nssm start VoiceLogTranscriber
nssm start VoiceLogWeb
```

`nssm stop <name>` / `nssm remove <name>` to stop/uninstall. NSSM's default
stop sequence sends a console control event (equivalent to Ctrl+C) before
escalating to a hard kill, which this app's recorder specifically relies on
to finalize whatever segment was in progress rather than dropping it - see
`Segmenter.close()` in the code if you're curious.

## 7. Nightly summaries via Task Scheduler

Open Task Scheduler -> Create Task:
- General: run whether user is logged on or not.
- Trigger: Daily, at whatever time (e.g. 3:00 AM).
- Settings: check "If the task fails, restart every" - e.g. every 15 minutes,
  up to 3 times. This covers a same-night transient failure (Ollama not
  ready yet, a network blip to the Mac Studio, etc.).
- Action: Start a program
  - Program: `C:\AudioRecorder\venv\Scripts\python.exe`
  - Arguments: `-m app.summarizer.main`
  - Start in: `C:\AudioRecorder`

With no `--date`, this summarizes yesterday *plus* any other day in the last
14 days that has recordings but still no summary - so even if a night's run
fails outright (and the Task Scheduler retries above are exhausted), the next
successful run backfills it automatically instead of leaving a permanent gap.
You can also trigger/regenerate one specific day's summary anytime from its
page in the web UI, and pick the `remote` (Mac Studio) backend there for a
deeper pass.

## Notes / limits

- The web UI has a single shared password with basic rate limiting on failed
  attempts (5 per 5 minutes) - it's meant for your home LAN, not the
  internet, and travels over plain HTTP. Don't port-forward it.
- The "regenerate summary" button runs the LLM call synchronously in the
  request - expect it to take anywhere from seconds to a few minutes
  depending on backend/model and how much you spoke that day.
- The keyword-flagging feature only looks at the same VAD-detected segment as
  the trigger phrase (typically a few seconds to under a minute) - it won't
  reach into the next segment if you say a trigger right at the end of one.
- A single conversation that runs longer than `vad.max_segment_seconds`
  (default 5 minutes) is force-split into consecutive recordings rather than
  buffered as one ever-growing clip - this also happens if the VAD gets stuck
  "triggered" by continuous background noise (TV/music left on).
- A conversation spanning midnight shows up (in full) under whichever day you
  click into it from, on both days' lists - this is deliberate: showing only
  half the conversation on each day's page would be worse than the
  cosmetic-only overlap in the day list.
