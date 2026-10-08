"""Measured progress records; no fabricated percentage for opaque stages."""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, cast

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import import_reason as ir
from portable_proc import group_kwargs, kill_tree

RELAYED = "CASTLE_PROGRESS "
#: What a job record keeps of a failed child's words, for Details.
LOG_CHARS = 8000

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# tools/stems.py prints one line per layer/channel analysis: six layers
# (vocals, backing, combined, drums, bass, other) x three channels.
_ANALYSIS_LINE = re.compile(
    r"\s*(vocals|backing|combined|drums|bass|other)\s+(left|right|both)\s"
)
ANALYSES = 18


class Cancelled(ValueError):
    """The listener cancelled this preparation; not a failure to report."""


class ToolFailed(ValueError):
    """A child that failed. str() is the owner's one sentence
    (tools/import_reason.py — the importer's own last line, when it printed
    one); `log` is what the child said, which the page keeps behind
    Details. The Rust studio's job ends the same way (studio_jobs.rs)."""

    def __init__(self, said, log):
        super().__init__(said)
        self.log = log[-LOG_CHARS:]


def failure(exc):
    """The page's fields for the exception that ended a job: the owner's
    sentence, the words behind it (Details), and the one-button fix when the
    sentence offers one — "Update the downloader". A Cancelled never comes
    here: cancelling is not a failure (grade report 2026-09-24 B6)."""
    if isinstance(exc, ToolFailed):
        said, log = str(exc), exc.log
    else:
        log = f"{type(exc).__name__}: {exc}"
        known = ir.for_os_error(exc) if isinstance(exc, OSError) else None
        said = known or ir.recognised(log) or ir.GENERIC
    return {"error": said, "error_detail": log[-LOG_CHARS:], "action": ir.action(said)}


def logged(line):
    """A line as the job's log keeps it. A relayed one (`CASTLE_PROGRESS
    {"line": …}`) is the yt-dlp or splitter line it carries, indented as a
    quote — the Rust studio's take_line does the same — so the reason scan
    never reads a download's warning as the reason a later step failed."""
    if line.startswith(RELAYED):
        try:
            carried = json.loads(line[len(RELAYED) :])["line"]
        except (ValueError, KeyError, TypeError):
            return line
        if isinstance(carried, str):
            return "    " + carried.rstrip() + "\n"
    return line


def ended(code):
    """The exit, for the log and never the sentence (studio_jobs.rs)."""
    return f"(the job ended with {'a signal' if code < 0 else f'exit {code}'})\n"


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


def found_title(clean):
    """The song's own name, the moment the downloader says it: the importer
    saves a link as `<title>.<ext>`, so the Destination line carries it long
    before the manifest does."""
    _, marker, path = clean.partition("Destination: ")
    if not marker or not clean.startswith(("[download]", "[ExtractAudio]")):
        return None
    return os.path.splitext(os.path.basename(path.strip()))[0][:200] or None


def interpret(line, stage, analyzed):
    """One line of a tool's output as a progress record, plus `found_title`
    when the line is the one that names the song."""
    values, analyzed = _progress(line, stage, analyzed)
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
    named = found_title(clean.partition('{"line": "')[2] or clean)
    return ({**values, "found_title": named} if named else values), analyzed


def _progress(line, stage, analyzed):
    if line.startswith(RELAYED):
        try:
            line = json.loads(line[len(RELAYED) :])["line"]
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
            "detail": "Saving voice, drum, bass and background previews",
        }, analyzed
    if _ANALYSIS_LINE.match(clean):
        analyzed += 1
        return {
            "phase": "Analyzing separated audio",
            "percent": min(100, analyzed / ANALYSES * 100),
            "detail": f"{analyzed} of {ANALYSES} layer/channel analyses finished",
        }, analyzed
    if "analysing" in clean.lower() or "analyzing" in clean.lower():
        return {
            "phase": "Analyzing audio",
            "percent": None,
            "detail": "Finding timing and rhythm",
        }, analyzed
    return {}, analyzed


def spawn(args, **kwargs):
    """Popen in its own process group, a child that cannot start said in
    the owner's words: a missing interpreter is a broken install."""
    try:
        return subprocess.Popen(args, **kwargs, **group_kwargs())
    except OSError as exc:
        said = ir.for_os_error(exc) or ir.START_FAILED
        raise ToolFailed(said, f"{Path(args[0]).name}: {exc}\n") from exc


def run(args, timeout, stage, report, extra_env=None, stop=None):
    """`stop` is an Event the caller sets to cancel: the child's whole process
    group is killed, as it is on a timeout, and the run raises Cancelled."""
    # The children are this repo's Python tools: PYTHONUTF8 makes them write
    # UTF-8 on Windows too (a song title in Japanese would otherwise die in
    # cp1252), and the pipe is read as UTF-8 on every system to match.
    env = {
        **os.environ,
        "CASTLE_PROGRESS_STREAM": "1",
        "PYTHONUTF8": "1",
        **(extra_env or {}),
    }
    process = spawn(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    # stdout=PIPE always opens one; the stubs cannot know that (the same
    # cast tools/progress_process.py makes for the same reason).
    output = cast(IO[str], process.stdout)
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        if process.poll() is None:
            kill_tree(process)

    timer = threading.Timer(timeout, expire)
    timer.start()

    def watch():
        while process.poll() is None:
            if stop.wait(0.25):
                expire()
                return

    if stop is not None:
        threading.Thread(target=watch, daemon=True).start()
    tail = []
    analyzed = 0
    started = time.time()
    report(started_at=started, percent=None, detail="Starting the audio tools")
    try:
        for line in output:
            tail.append(logged(line))
            tail = tail[-100:]
            values, analyzed = interpret(line.strip(), stage, analyzed)
            if values:
                report(**values)
        code = process.wait()
        if stop is not None and stop.is_set():
            raise Cancelled("Cancelled")
        log = "".join(tail)
        if timed_out.is_set():
            # A stall is the cause whatever the last line says: that line
            # is only where the child was when it was stopped.
            stopped = f"(stopped: timed out after {timeout:g} s)\n"
            raise ToolFailed(ir.STALLED, log + stopped)
        if code:
            raise ToolFailed(ir.reason(log) or ir.GENERIC, log + ended(code))
        return log
    finally:
        timer.cancel()
        output.close()
        if process.poll() is None:
            expire()
            process.wait()


def runner(stop=None, timeout=600.0):
    """A stand-in for `subprocess.run` that `stop` can end, for the children
    whose OUTPUT is the answer — analyze_track's JSON — where `run`'s
    line-by-line progress reading does not apply. The tools that spawn them
    (import_track.crate_analysis, and render_cues.waveform through
    rich_show.prepare) take it as their `run=`, so a Cancel that arrives while the
    light show is being built kills that child's whole group and raises
    Cancelled, just as it does mid-download (grade report 2026-09-24 B6).
    `timeout` is per child: none of them had one."""

    def call(args, *, input=None, capture_output=False, check=False, **kwargs):
        pipe = subprocess.PIPE if capture_output else None
        stdin = subprocess.PIPE if input is not None else None
        deadline = time.monotonic() + timeout
        with spawn(args, stdin=stdin, stdout=pipe, stderr=pipe, **kwargs) as process:
            pending = input  # communicate takes the input on its first call only
            while True:
                try:
                    out, err = process.communicate(pending, timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
                    late = time.monotonic() > deadline
                    if not late and (stop is None or not stop.is_set()):
                        continue
                    kill_tree(process)
                    process.communicate()
                    if late:
                        log = f"{Path(args[0]).name} timed out after {timeout:g} s\n"
                        raise ToolFailed(ir.STALLED, log) from None
                    raise Cancelled("Cancelled") from None
        result = subprocess.CompletedProcess(args, process.returncode, out, err)
        if check:
            result.check_returncode()
        return result

    return call
