"""Which host C++ compiler builds the firmware's card and server harnesses.

The firmware is written against ESP-IDF's newlib: <dirent.h>, <unistd.h>,
<strings.h>, `fopen` without a deprecation lecture. tests/cxx/shim/ stands
that platform up on the host, and on macOS and Linux the host's own libc is
already most of it, so clang++ is asked first, as it always was.

Windows is the exception. The clang++ on a Windows PATH targets the MSVC
runtime, which has none of those headers and deprecates `fopen` under
-Werror. MinGW-w64's g++ ships the POSIX half (and is the compiler family
the device itself is built with — xtensa-esp32s3-elf-g++), so there it is
asked first; the shim's few remaining Windows gaps (`d_type`, statvfs, a
binary stdin) are filled in tests/cxx/shim/ itself, behind `_WIN32`.

The pure-arithmetic harnesses (effects, pixels, the audio clock) compile
cleanly against either runtime and keep their own clang-first lookup; this
module is for the ones that touch the card, the pipes or the environment.
"""

from __future__ import annotations

import os
import shutil


def find(windows: bool | None = None) -> str | None:
    """The compiler's path, or None when this machine has neither."""
    nt = os.name == "nt" if windows is None else windows
    order = ("g++", "clang++") if nt else ("clang++", "g++")
    for name in order:
        path = shutil.which(name)
        if path:
            return path
    return None


#: The compiler the card/pipe harnesses use (None: there is none).
COMPILER = find()
