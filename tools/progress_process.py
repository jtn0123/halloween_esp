"""Opt-in streaming progress while retaining subprocess.run-compatible results.

Each pipe is drained by its own thread into one queue rather than through a
selector: `select()` on Windows takes sockets only, never pipes, so the
selector loop this replaced could not run there at all. Threads read the
same bytes the same way on every system.
"""

from __future__ import annotations

import codecs
import json
import queue
import subprocess
import threading
import time
from typing import BinaryIO, cast

from portable_proc import group_kwargs, kill_tree, utf8_env

#: One read's worth from a pipe; also how often the timeout is looked at.
CHUNK = 8192
POLL = 0.2


def _kill(process: subprocess.Popen[bytes]) -> None:
    kill_tree(process)
    process.wait()


def _emit_lines(pending: str) -> str:
    """Print each complete line as a progress record; return the remainder."""
    while "\n" in pending:
        line, pending = pending.split("\n", 1)
        if line.strip():
            print("CASTLE_PROGRESS " + json.dumps({"line": line.strip()}), flush=True)
    return pending


def _pump(stream: BinaryIO, name: str, chunks: queue.Queue[tuple[str, bytes]]) -> None:
    """Copy one pipe into the queue, then close it (here, by the only thread
    that reads it); an empty chunk tells the reader the pipe is done."""
    try:
        while chunk := stream.read1(CHUNK):  # type: ignore[attr-defined]
            chunks.put((name, chunk))
    except OSError:
        pass  # a killed child can leave a broken pipe rather than an EOF
    finally:
        stream.close()
        chunks.put((name, b""))


def run_progress(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    # Decoded as UTF-8 below, so the child is told to write UTF-8.
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=utf8_env(),
        **group_kwargs(),
    )
    buffers = {"stdout": "", "stderr": ""}
    pending = {"stdout": "", "stderr": ""}
    decoders = {
        name: codecs.getincrementaldecoder("utf-8")(errors="replace")
        for name in buffers
    }
    chunks: queue.Queue[tuple[str, bytes]] = queue.Queue()
    for name in buffers:
        stream = cast(BinaryIO, getattr(process, name))
        threading.Thread(target=_pump, args=(stream, name, chunks), daemon=True).start()
    started = time.monotonic()
    open_streams = len(buffers)
    try:
        while open_streams:
            left = timeout - (time.monotonic() - started)
            if left <= 0:
                _kill(process)
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                name, chunk = chunks.get(timeout=min(POLL, left))
            except queue.Empty:
                continue
            if not chunk:
                open_streams -= 1
                continue
            text = decoders[name].decode(chunk)
            buffers[name] += text
            pending[name] = _emit_lines(pending[name] + text.replace("\r", "\n"))
        code = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        return subprocess.CompletedProcess(
            args, code, buffers["stdout"], buffers["stderr"]
        )
    finally:
        if process.poll() is None:
            _kill(process)
