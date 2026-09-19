#!/usr/bin/env python3
"""Dev convenience launcher: runs recorder, transcriber, and web as subprocesses,
restarting any that crash. For production on the mini PC, register each as an
NSSM service instead (see SETUP.md) so they survive reboots/logouts."""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["app.recorder.main", "app.transcriber.main", "app.web.main"]
RESTART_DELAY_SEC = 5
GRACEFUL_STOP_TIMEOUT_SEC = 15

# Needed on Windows so CTRL_BREAK_EVENT can be targeted at just one child
# process rather than this whole console's process group (which would
# include run_all.py itself).
_CREATIONFLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

procs: dict[str, subprocess.Popen] = {}


def start(module: str) -> None:
    print(f"[run_all] starting {module}")
    procs[module] = subprocess.Popen(
        [sys.executable, "-m", module], cwd=str(ROOT), creationflags=_CREATIONFLAGS
    )


def stop_all(*_args) -> None:
    # Ask nicely first: Popen.terminate() alone calls TerminateProcess() on
    # Windows, which can't be caught by any code and would drop whatever the
    # recorder had buffered but not yet written. CTRL_BREAK_EVENT (Windows) /
    # SIGTERM (POSIX) let each process's own graceful-shutdown handling run
    # (see recorder/main.py) - a hard kill is only the fallback.
    print("[run_all] stopping all processes")
    for proc in procs.values():
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
        except Exception:
            proc.terminate()

    for proc in procs.values():
        try:
            proc.wait(timeout=GRACEFUL_STOP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            print("[run_all] a process didn't stop gracefully in time, killing it")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    for module in MODULES:
        start(module)

    while True:
        time.sleep(2)
        for module, proc in list(procs.items()):
            if proc.poll() is not None:
                print(f"[run_all] {module} exited ({proc.returncode}); "
                      f"restarting in {RESTART_DELAY_SEC}s")
                time.sleep(RESTART_DELAY_SEC)
                start(module)


if __name__ == "__main__":
    main()
