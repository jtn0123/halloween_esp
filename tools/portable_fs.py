"""The two filesystem habits that differ between macOS and Windows: an
exclusive cross-process lock, and the atomic rename.

LOCK. tools/manifest.py and core/src/manifest.rs serialise their
read-modify-write of tracks.json on ONE file, `tracks.lock`, exclusively and
over the whole file. On POSIX that is flock(2), as it always was. On Windows
it is `msvcrt.locking` over [0, 2^31-1) from offset 0 — a byte-range lock,
mandatory, and overlapping the range Rust's `std::fs::File::lock` takes
(LockFileEx from offset 0, length u64::MAX), so a Python child and the Rust
studio still exclude each other. The file is opened without truncation, as
the Rust side opens it, and nothing ever reads or writes its bytes.
`msvcrt.LK_LOCK` gives up after ten one-second tries; the loop below keeps
asking, because the caller asked for a lock and not for a timeout.

RENAME. `os.replace` is atomic on both systems, but Windows refuses it
(PermissionError, "being used by another process") while ANY process holds
the destination open — a reader halfway through the old copy, a virus
scanner, the search indexer. That window is milliseconds, so a short retry
is the whole fix; past it, the error is real and is raised.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: msvcrt.locking's byte count is a C long: the most a single call can hold.
LOCK_SPAN = 0x7FFF_FFFF
#: os.replace retry: ~1 s in all, far longer than a reader holds a file.
REPLACE_TRIES = 20
REPLACE_PAUSE = 0.05


def _lock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, LOCK_SPAN)
                return
            except OSError:
                continue  # LK_LOCK's ten seconds ran out; keep waiting
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, LOCK_SPAN)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive whole-file lock on `path` (created if missing,
    never truncated) for the duration of the block."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock_fd(fd)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """os.replace, retried briefly while Windows says the target is busy."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE)
