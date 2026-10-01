"""Start a child in its own process group, and kill the whole group later —
on macOS/Linux and on Windows.

Why a group at all: the importer's children have children of their own
(yt-dlp starts ffmpeg, demucs starts worker processes). Killing only the
child on a timeout or a cancel leaves those grandchildren running, holding
the scratch files and the CPU. On POSIX the child leads a new session and
`os.killpg` takes the session's group down. Windows has no process groups
in that sense: `CREATE_NEW_PROCESS_GROUP` detaches the child from our
console's Ctrl+C, and `taskkill /T /F` walks the parent→child tree from the
child's pid and kills every process in it.

Known gap on Windows: taskkill finds descendants through their parent pid,
so a grandchild whose parent has ALREADY exited is no longer reachable. A
job object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) closes that; it is the
desktop app's job (docs/PRODUCTION-TODO.md §4.1, the Rust studio's twin).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any


def group_kwargs() -> dict[str, Any]:
    """The Popen keywords that put the child in a group of its own."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(process: subprocess.Popen[Any]) -> None:
    """Kill `process` and every process it started; quiet if it is gone."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.kill()  # taskkill raced an exit, or is missing
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # the whole group already exited
