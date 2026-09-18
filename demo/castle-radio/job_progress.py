"""Measured progress records; no fabricated percentage for opaque stages."""

import json
import os
import re
import signal
import subprocess
import threading
import time
from typing import IO, cast

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def percent_value(text):
    """The number written just before the first `%` (`41.8% of` is 41.8),
    clamped to 0..100; None when no percentage is on the line. Splitting on
    the sign first keeps the parse linear: the number is matched whole,
    never searched for."""
    for chunk in text.split("%")[:-1]:
        tail = chunk[len(chunk.rstrip("0123456789.")) :].lstrip(".")
        if _NUMBER.fullmatch(tail):
            return min(100, max(0, float(tail)))
    return None


def interpret(line, stage, analyzed):
    if line.startswith("CASTLE_PROGRESS "):
        try:
            line = json.loads(line[len("CASTLE_PROGRESS ") :])["line"]
        except (ValueError, KeyError):
            return {}, analyzed
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
    value = percent_value(clean)
    if value is not None and (
        "[download]" in clean or (stage == "split" and "|" in clean)
    ):
        return {
            "phase": "Downloading audio"
            if "[download]" in clean
            else "Separating voice and background",
            "percent": value,
            "detail": clean[-220:],
        }, analyzed
    if "ExtractAudio" in clean or "[ffmpeg]" in clean:
        return {
            "phase": "Converting audio",
            "percent": None,
            "detail": "Preparing the playable audio file",
        }, analyzed
    if "encoding stems" in clean:
        return {
            "phase": "Encoding separated audio",
            "percent": None,
            "detail": "Saving voice and background previews",
        }, analyzed
    if re.match(r"\s*(vocals|backing|combined)\s+(left|right|both)\s", clean):
        analyzed += 1
        return {
            "phase": "Analyzing separated audio",
            "percent": min(100, analyzed / 9 * 100),
            "detail": f"{analyzed} of 9 layer/channel analyses finished",
        }, analyzed
    if "analysing" in clean.lower() or "analyzing" in clean.lower():
        return {
            "phase": "Analyzing audio",
            "percent": None,
            "detail": "Finding timing and rhythm",
        }, analyzed
    return {}, analyzed


def run(args, timeout, stage, report, extra_env=None):
    env = {**os.environ, "CASTLE_PROGRESS_STREAM": "1", **(extra_env or {})}
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    # stdout=PIPE always opens one; the stubs cannot know that (the same
    # cast tools/progress_process.py makes for the same reason).
    output = cast(IO[str], process.stdout)
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    timer = threading.Timer(timeout, expire)
    timer.start()
    tail = []
    analyzed = 0
    started = time.time()
    report(started_at=started, percent=None, detail="Starting the audio tools")
    try:
        for line in output:
            tail.append(line)
            tail = tail[-100:]
            values, analyzed = interpret(line.strip(), stage, analyzed)
            if values:
                report(**values)
        code = process.wait()
        if timed_out.is_set():
            raise ValueError("Preparation timed out. Retry this song.")
        if code:
            raise ValueError("".join(tail)[-1800:] or "Audio preparation failed")
        return "".join(tail)
    finally:
        timer.cancel()
        output.close()
        if process.poll() is None:
            expire()
            process.wait()
