"""The buyer's first session, through Castle Radio's own HTTP routes.

What the page does when a person uses it — Find my castle with a typed
address, Add songs (the raw upload the import panel sends), Send to castle,
play — done by tests/install_smoke.py against an INSTALLED Castle Radio and
an emulated castle (tools/castle_emu.py). The routes and headers are the
page's own (imports.js, remote-library.js, castle-find.js,
device-link.js); nothing here reaches past them into the server's files,
except to read the card afterwards the way the castle would.
"""

from __future__ import annotations

import itertools
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import cue_file

#: The phase Castle Radio shows while Demucs itself runs (job_progress.py),
#: and the two that follow it inside the same split step.
SEPARATING = "Separating voice and background"
SPLIT_PHASES = (SEPARATING, "Encoding separated audio", "Analyzing separated audio")
#: How long one song may take to prepare on a cold CI runner, Demucs included.
JOB_SECONDS = 1200


@dataclass(frozen=True)
class Song:
    """One file the buyer drags in: its name on their disk, the length the
    test makes it, and whether the import asks for the voice split."""

    name: str
    seconds: int
    split: bool
    pitch: float


#: An mp3, a wav, and a name in two alphabets with a dash Windows' ANSI code
#: page has and a character it does not. The split one is ~10 s: the
#: Demucs timing is for that length.
SONGS = (
    Song("smoke tone.mp3", 8, False, 220.0),
    Song("smoke tone.wav", 8, False, 330.0),
    Song("Ünïcødé – 鬼.mp3", 10, True, 147.0),  # noqa: RUF001 — the en dash IS the test
)


def tone_command(ffmpeg: str, out: Path, song: Song) -> list[str]:
    """ffmpeg making `song`: a sine plucked twice a second over a second
    sine an octave up, so the onset analysis has beats to find."""
    p = song.pitch
    expr = (
        f"0.5*sin(2*PI*{p}*t)*exp(-6*mod(t\\,0.5))"
        f"+0.25*sin(2*PI*{p * 2.01:.2f}*t)*exp(-3*mod(t+0.25\\,1))"
    )
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d={song.seconds}",
        "-ac", "2", str(out),
    ]  # fmt: skip


def page_filename(name: str) -> str:
    """encodeURIComponent(name), as imports.js sends X-Filename."""
    return urllib.parse.quote(name, safe="!'()*")


def page_title(name: str) -> str:
    """The title Castle Radio gives an upload: the stem, composed (NFC)."""
    return unicodedata.normalize("NFC", Path(name).stem)[:200]


def phase_spans(samples: list[tuple[float, str]]) -> dict[str, float]:
    """Seconds spent in each phase, from (time, phase) polls in order: each
    poll's phase is charged until the next poll."""
    spans: dict[str, float] = {}
    for (t0, phase), (t1, _next) in itertools.pairwise(samples):
        spans[phase] = spans.get(phase, 0.0) + (t1 - t0)
    return spans


class Radio:
    """Castle Radio at `base`, spoken to as its page speaks to it."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 60,
    ) -> tuple[int, Any]:
        req = urllib.request.Request(
            self.base + path,
            data=body,
            headers=dict(headers or {}),
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode("utf-8", errors="replace")

    def get(self, path: str, timeout: float = 60) -> Any:
        status, answer = self.call(path, timeout=timeout)
        if status != 200:
            raise SystemExit(f"GET {path}: {status} {answer}")
        return answer

    def post(self, path: str, body: Mapping[str, Any], timeout: float = 60) -> Any:
        status, answer = self.call(
            path,
            json.dumps(dict(body)).encode("utf-8"),
            {"Content-Type": "application/json"},
            timeout,
        )
        if status not in (200, 202):
            raise SystemExit(f"POST {path} {dict(body)}: {status} {answer}")
        return answer

    def upload(self, path: Path, song: Song) -> dict[str, Any]:
        """The import panel's raw upload (imports.js, the desktop branch)."""
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Castle": "1",
            "X-Filename": page_filename(song.name),
            "X-Split": "true" if song.split else "false",
            "X-Audio-Format": "mp3",
            "X-Audio-Quality": "standard",
        }
        status, job = self.call("/radio/import", path.read_bytes(), headers, 300)
        if status != 202 or not isinstance(job, dict):
            raise SystemExit(f"import {song.name!r}: {status} {job}")
        return job

    def wait_jobs(
        self, ids: list[str], seconds: float = JOB_SECONDS
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple[float, str]]]]:
        """Poll /radio/jobs as the queue does until every job is done: the
        final records, and each job's (time, phase) trail."""
        trail: dict[str, list[tuple[float, str]]] = {i: [] for i in ids}
        final: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            now = time.monotonic()
            jobs = {str(j["id"]): j for j in self.get("/radio/jobs", 30)}
            for i in ids:
                job = jobs.get(i)
                if job is None:
                    raise SystemExit(f"job {i} vanished from /radio/jobs")
                if not trail[i] or trail[i][-1][1] != job["phase"]:
                    print(f"  {now:9.2f}  {job.get('title') or i}: {job['phase']}")
                trail[i].append((now, str(job["phase"])))
                if job["done"]:
                    final[i] = job
            if len(final) == len(ids):
                return final, trail
            time.sleep(0.25)
        raise SystemExit(f"imports not finished after {seconds:.0f} s")

    def sync(self, key: str, seconds: float = 300) -> dict[str, Any]:
        """Send to castle (remote-library.js): start, then poll the job."""
        job: dict[str, Any] = self.post("/radio/device/sync", {"key": key})
        deadline = time.monotonic() + seconds
        while not job.get("done"):
            if time.monotonic() > deadline:
                raise SystemExit(f"sync of {key} not done after {seconds:.0f} s")
            time.sleep(0.25)
            query = urllib.parse.urlencode({"key": key})
            job = self.get(f"/radio/device/sync-status?{query}", 30)
        return job


def card_problems(card: Path, library: Path, filename: str) -> list[str]:
    """What is wrong with one synced song on the emulated card: its audio
    and both show files present and byte-equal to the library's, and a .cue
    the firmware's loader would accept with cues in it."""
    problems = []
    stem = Path(filename).stem
    for name in (filename, f"{stem}.show.json", f"{stem}.cue"):
        on_card, local = card / name, library / name
        if not on_card.is_file():
            problems.append(f"{name} is not on the card")
        elif not local.is_file():
            problems.append(f"{name} is not in the library to compare")
        elif on_card.read_bytes() != local.read_bytes():
            problems.append(f"{name} on the card differs from the library's")
    if (card / f"{stem}.cue").is_file() and cue_file.loaded_count(
        card / f"{stem}.cue"
    ) == 0:
        problems.append(f"{stem}.cue holds no cues the castle would load")
    show = card / f"{stem}.show.json"
    if show.is_file():
        try:
            json.loads(show.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{show.name} is not JSON: {exc}")
    return problems


def wait_playing(port: int, filename: str) -> dict[str, Any]:
    """The castle's own answer once it plays `filename` (the 200 ms tick
    applies a command after the reply, as the firmware does)."""
    state: dict[str, Any] = {}

    def playing() -> bool:
        nonlocal state
        state = se.castle_status(port)
        return bool(state.get("track") == filename)

    se.wait_for(playing, 10, f"the castle to play {filename}")
    return state
