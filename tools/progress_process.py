"""Opt-in streaming progress while retaining subprocess.run-compatible results."""

from __future__ import annotations

import codecs
import json
import os
import selectors
import signal
import subprocess
import time
from typing import BinaryIO, cast


def run_progress(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    selector = selectors.DefaultSelector()
    buffers = {"stdout": "", "stderr": ""}
    pending = {"stdout": "", "stderr": ""}
    decoders = {
        name: codecs.getincrementaldecoder("utf-8")(errors="replace")
        for name in buffers
    }
    for name in buffers:
        selector.register(getattr(process, name), selectors.EVENT_READ, name)
    started = time.monotonic()
    try:
        while selector.get_map():
            if time.monotonic() - started > timeout:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise subprocess.TimeoutExpired(args, timeout)
            for key, _ in selector.select(0.2):
                stream = cast(BinaryIO, key.fileobj)
                chunk = os.read(stream.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    stream.close()
                    continue
                name = key.data
                text = decoders[name].decode(chunk)
                buffers[name] += text
                pending[name] += text.replace("\r", "\n")
                while "\n" in pending[name]:
                    line, pending[name] = pending[name].split("\n", 1)
                    if line.strip():
                        print(
                            "CASTLE_PROGRESS " + json.dumps({"line": line.strip()}),
                            flush=True,
                        )
        code = process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
        return subprocess.CompletedProcess(
            args, code, buffers["stdout"], buffers["stderr"]
        )
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
