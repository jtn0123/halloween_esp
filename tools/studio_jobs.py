#!/usr/bin/env python3
"""Background import jobs, so a long download does not look like a hang.

A 40-minute video takes a while to fetch. The old import blocked the HTTP
request for the whole thing, which meant the page sat on "Importing…" with no
way to tell a slow download from a dead one — and any other request queued
behind it, so even the track list stopped responding.

Now a job starts, returns an id immediately, and reports progress as yt-dlp
prints it. Polling rather than server-sent events: the client already polls
for other things, it survives a dropped connection without reconnect logic,
and a progress bar does not need sub-second latency.
"""

from __future__ import annotations

import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

Phase = Literal["queued", "fetching", "converting", "analysing", "done", "failed"]

# yt-dlp's progress line, e.g. "[download]  41.8% of 2.39MiB at 15.81MiB/s ETA 00:00"
_PROGRESS = re.compile(
    r"\[download\]\s+(?P<pct>[\d.]+)%\s+of\s+~?\s*(?P<size>\S+)"
    r"(?:\s+at\s+(?P<rate>\S+))?(?:\s+ETA\s+(?P<eta>\S+))?")


@dataclass
class Job:
    id: str
    phase: Phase = "queued"
    percent: float = 0.0
    detail: str = ""
    log: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "phase": self.phase, "percent": round(self.percent, 1),
            "detail": self.detail, "error": self.error,
            "done": self.phase in ("done", "failed"),
            # The tail is enough to diagnose a failure without shipping the
            # whole of yt-dlp's chatter to the browser.
            "log": self.log[-40:],
        }


class JobRunner:
    """One import at a time; ffmpeg and yt-dlp are not worth interleaving."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, argv: list[str]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
            # Keep the map from growing without bound over a long session.
            if len(self._jobs) > 40:
                for k in [k for k, v in self._jobs.items()
                          if v.phase in ("done", "failed")][:20]:
                    del self._jobs[k]
        threading.Thread(target=self._run, args=(job, argv), daemon=True).start()
        return job

    def _run(self, job: Job, argv: list[str]) -> None:
        job.phase = "fetching"
        try:
            # Context-managed so the child's stdout is closed deterministically.
            # Leaving it to the collector worked, but raised a ResourceWarning
            # on every job, which is noise that trains you to ignore warnings.
            with subprocess.Popen(argv, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  bufsize=1) as p:
                # Wall-clock kill: a child that stops producing output blocks
                # the readline below forever, and the job would sit at
                # "fetching" for the life of the server.
                watchdog = threading.Timer(900, p.kill)
                watchdog.start()
                try:
                    assert p.stdout is not None
                    for raw in p.stdout:
                        line = raw.rstrip()
                        if line:
                            job.log.append(line)
                        self._interpret(job, line)
                    code = p.wait()
                finally:
                    watchdog.cancel()
        except OSError as e:
            job.phase, job.error = "failed", str(e)
            return
        if code == 0:
            job.phase, job.percent, job.detail = "done", 100.0, ""
        else:
            job.phase = "failed"
            job.error = self._explain(job.log) or (
                "gave up after 15 minutes — the job stalled" if code == -9
                else f"import failed (exit {code})")

    @staticmethod
    def _interpret(job: Job, line: str) -> None:
        m = _PROGRESS.search(line)
        if m:
            job.phase = "fetching"
            job.percent = float(m.group("pct"))
            rate, eta = m.group("rate"), m.group("eta")
            job.detail = f"{m.group('size')}"
            if rate:
                job.detail += f" at {rate}"
            if eta:
                job.detail += f", {eta} left"
            return
        if "ExtractAudio" in line or line.startswith("[ffmpeg]"):
            job.phase, job.percent, job.detail = "converting", 100.0, "extracting audio"
        elif line.startswith("imported "):
            job.phase, job.detail = "analysing", "detecting onsets"

    @staticmethod
    def _explain(log: list[str]) -> str:
        """Turn yt-dlp's output into something worth showing a person.

        Raw shell output in a UI is a failure of nerve — the interesting line
        is almost always in there, it just needs finding.
        """
        text = "\n".join(log)
        known = [
            ("Private video", "That video is private."),
            ("Video unavailable", "That video is unavailable."),
            ("Sign in to confirm", "That video needs a signed-in account."),
            ("members-only", "That video is members-only."),
            ("is not a valid URL", "That does not look like a link."),
            ("Unsupported URL", "Nothing here knows how to read that link."),
            ("HTTP Error 404", "That link is a dead end (404)."),
            ("no audio file", "The download produced no audio."),
            ("Requested format", "No audio-only format was offered for that video."),
        ]
        for needle, friendly in known:
            if needle.lower() in text.lower():
                return friendly
        for line in reversed(log):
            if "ERROR" in line:
                return line.split("ERROR:", 1)[-1].strip() or line
        return ""
