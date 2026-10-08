"""The installer's progress lines — a protocol with the desktop app.

On its first launch the desktop app runs tools/desktop_install.py with
`--progress` and reads its standard output line by line
(desktop/src-tauri/src/setup_run.rs). Two kinds of line mean something to
it; everything else only goes to the app's log:

    @castle-step N/M <what>     step N of M begins: shown on the splash page
    @castle-failed <why>        the install stopped: shown as the reason

The step names are written for the owner, because the splash shows them as
they are. tests/test_desktop_bundle.py holds setup_run.rs's copies of the
two markers to these.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

STEP_MARK = "@castle-step"
FAILED_MARK = "@castle-failed"
#: In the order desktop_install.Installer.install runs them.
STEPS = (
    "Copying Castle Tools' own files",
    "Installing Python and its packages — the big download, several hundred MB",
    "Placing the castle-core programs",
    "Getting ffmpeg",
    "Getting the song downloader",
    "Getting the voice splitter's model",
    "Preparing your song library",
    "Finishing up",
)


def step_line(n: int) -> str:
    """The line that says step `n` (1-based) of STEPS begins."""
    return f"{STEP_MARK} {n}/{len(STEPS)} {STEPS[n - 1]}"


def failure(exc: Exception) -> str:
    """The reason in one line: a failed program by its name and status, not
    its whole command line (that is in the log, above)."""
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = exc.cmd if isinstance(exc.cmd, list) else str(exc.cmd).split()
        return f"{Path(str(cmd[0])).name} stopped with status {exc.returncode}"
    return " ".join(str(exc).split())


def failed_line(exc: Exception) -> str:
    return f"{FAILED_MARK} {failure(exc)}"
