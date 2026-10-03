"""The operator's own commands — a smart plug's CLI, a curl to a router plug.

tools/power_cycle.py (--off-cmd / --on-cmd) and tools/soak.py (--disrupt-cmd)
switch things the harness does not know how to switch: the operator names a
command on the command line, and running it is the feature. It runs as a
program with its arguments and no shell in between — nothing in it is
expanded, chained or redirected — so `&&`, pipes and `sleep` are not
available, and anything that needs them is a script the command names.

The line is split the way the platform writes one: POSIX quoting on macOS
and Linux (shlex), and on Windows the C runtime's (a double-quoted word is
one argument and a backslash is a path separator, not an escape).
"""

from __future__ import annotations

import os
import shlex
import subprocess

_QUOTES = "\"'"


def argv_of(cmd: str, posix: bool | None = None) -> list[str]:
    """`cmd` as the program and its arguments (posix: default this OS's)."""
    if posix is None:
        posix = os.name != "nt"
    words = shlex.split(cmd, posix=posix)
    if posix:
        return words
    # Non-POSIX shlex keeps a quoted word's quotes; the program wants the word.
    return [
        w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in _QUOTES else w
        for w in words
    ]


def run(cmd: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one operator command to completion, its output captured.

    Raises ValueError for a line with no program in it, OSError when the
    program cannot be started and subprocess.TimeoutExpired past `timeout`.
    """
    argv = argv_of(cmd)
    if not argv:
        raise ValueError("the command is empty")
    # The operator's own plug command from their own argv, run with no shell:
    # running it is what the flag is for (S8701 is reviewed, not a leak).
    return subprocess.run(  # NOSONAR — runs the operator's own command, no shell
        argv,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
