"""The golden-fixture harness: one studio, one sandbox, one recorded answer.

Why this exists alongside studio_rust_case.py. That fixture holds the two
servers answer-for-answer over twin sandboxes — the strongest check we have,
and the one that dies with `tools/studio.py` when the Python studio is
retired off-season (after Halloween 2026). What must NOT die with it is the
evidence: the exact bytes the trusted implementation answered. So this module
drives ONE server over ONE sandbox and writes its answers down
(`tools/gen_golden.py`, run against the Python studio while it is still the
reference); `tests/test_studio_golden.py` then replays the same script against
the Rust studio and diffs. After the retirement the goldens are the contract,
and a future native-Rust validator — one that no longer shells out to
`tools/scene_check.py` — has something to be written against.

Deliberately self-contained: it shares no code with studio_rust_case.py even
where the two overlap (fetch, wait_up, free_port, the scenes fixture), because
that module is scheduled for deletion and a golden harness that follows it
into the grave would be worse than the duplication. `helpers.make_click_track`
is the one import from the live suite, and it outlives the Python studio.

The corpus itself — which routes, which malformed scene blocks — is
`golden_corpus.py`: the half that grows, kept apart from the machinery so
neither file spends its budget on the other's changes.

Only the DETERMINISTIC surface is recorded. Nothing here spawns ffmpeg,
yt-dlp or Demucs, nothing splices a scene (a successful splice rebuilds the
show and its log carries sandbox paths and timings), and the one listing that
does decode audio is recorded as its SHAPE rather than its numbers — see
`_tracks_shape`. A golden that drifts with the machine is a golden that gets
regenerated to make a failure go away, which is no golden at all.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from golden_corpus import (
    CEILING_CASE,
    JSON_HDRS,
    READ_CASES,
    SCENE_CASES,
    scenes_fixture,
    tiny,
)
from helpers import make_click_track

#: Where the recorded answers live. One file per group, JSON, sorted keys —
#: a golden nobody can read in a diff is a golden nobody reviews.
GOLDEN = ROOT / "tests" / "golden"
READ_FILE = GOLDEN / "read_routes.json"
SCENE_FILE = GOLDEN / "scene_errors.json"

# --------------------------------------------------------------------------
# Driving one server
# --------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def fetch(
    port: int,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method=method,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def wait_up(port: int, deadline_s: float = 45.0) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            fetch(port, "/api/status")
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"server on {port} never answered")


class Sandbox:
    """One studio's world: a one-track library, a two-scene show, an empty
    build root. Never the repo's own — the four CASTLE_* knobs are set
    explicitly, and CASTLE_HOST="" means "there is no castle", so not one
    socket leaves the machine (CLAUDE.md's sandboxing section)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tracks = root / "tracks"
        self.scenes = root / "scenes.yaml"
        self.build = root / "build"

    def seed(self) -> None:
        self.tracks.mkdir(parents=True, exist_ok=True)
        make_click_track(self.tracks / "t_alpha.wav", seconds=2.0)
        self.scenes.write_text(scenes_fixture())
        (self.build / "audio").mkdir(parents=True, exist_ok=True)
        (self.build / "previewer").mkdir(parents=True, exist_ok=True)

    def env(self, base: dict[str, str]) -> dict[str, str]:
        return {
            **base,
            "CASTLE_HOST": "",
            "CASTLE_TRACKS": str(self.tracks),
            "CASTLE_SCENES": str(self.scenes),
            "CASTLE_BUILD": str(self.build),
        }

    def fill_to_the_ceiling(self, limit: int) -> None:
        """Rewrite the show as `limit` scenes, so the next id is refused."""
        head = scenes_fixture().split("\nscenes:\n", 1)[0]
        body = "\n".join(tiny(f"s{i}") + "\n" for i in range(limit))
        self.scenes.write_text(head + "\nscenes:\n" + body)


def _tracks_shape(body: Any) -> Any:
    """The listing's SHAPE, not its numbers.

    `/studio/tracks` decodes what it has not analysed yet, so the onset
    counts and durations are numpy's answers on this machine's numpy. The
    contract the desk actually depends on is which fields come back and of
    what type — `scenes`, and every track carrying `id`/`ext`/`kb`/… — and
    that is stable everywhere. The live-twin suites still compare the
    numbers themselves while both servers exist.
    """
    if not isinstance(body, dict):
        return body
    tracks = body.get("tracks")
    shaped: list[Any] = []
    for t in tracks if isinstance(tracks, list) else []:
        if not isinstance(t, dict):
            shaped.append(type(t).__name__)
            continue
        shaped.append(
            {"id": t.get("id"), "fields": {k: type(v).__name__ for k, v in t.items()}}
        )
    return {"tracks": shaped, "scenes": body.get("scenes")}


def _record(raw: tuple[int, dict[str, str], bytes], path: str) -> dict[str, Any]:
    status, headers, data = raw
    try:
        body: Any = json.loads(data)
    except ValueError:
        # A non-JSON answer on this surface would itself be the finding, so
        # it is recorded rather than raised.
        body = {"__raw__": data.decode("utf-8", "replace")}
    # Only a successful listing is shaped: a 404 on the same path (the
    # wrong-verb case) is an error body, and reshaping it would record
    # "no tracks" where the contract is "not found".
    if status == 200 and path in ("/studio/tracks", "/api/tracks"):
        body = _tracks_shape(body)
    return {
        "status": status,
        "content_type": headers.get("content-type", ""),
        "body": body,
    }


def capture_read(port: int) -> dict[str, Any]:
    """Every read case, in one dict keyed by case name."""
    out: dict[str, Any] = {}
    for name, method, path in READ_CASES:
        out[name] = {
            "request": {"method": method, "path": path},
            **_record(fetch(port, path, method), path),
        }
    return out


def capture_scene_errors(port: int, box: Sandbox, limit: int) -> dict[str, Any]:
    """Every refusal the splice route can give, including the ceiling.

    The ceiling case comes last and leaves the sandbox's scenes.yaml full:
    nothing after it would be measuring the two-scene fixture any more, and
    the sandbox is thrown away at the end of the run either way.
    """
    out: dict[str, Any] = {}
    for name, body in SCENE_CASES:
        raw = fetch(port, "/studio/scene", "POST", JSON_HDRS, body)
        rec = {"request": {"body": body.decode()}, **_record(raw, "/studio/scene")}
        out[name] = _clip_detail(rec) if name in CLIPPED_DETAIL else rec
    box.fill_to_the_ceiling(limit)
    name, body = CEILING_CASE
    raw = fetch(port, "/studio/scene", "POST", JSON_HDRS, body)
    out[name] = {"request": {"body": body.decode()}, **_record(raw, "/studio/scene")}
    return out


def serialize(data: dict[str, Any]) -> str:
    """Stable bytes: sorted keys, two-space indent, one trailing newline —
    so regenerating with nothing changed is a no-op in the diff."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


#: Cases whose error string ends in a detail each LANGUAGE writes for
#: itself. `{nope` is refused by the studio's own JSON parser — Python's
#: json module and the Rust studio's parser both say so, in their own
#: words ("Expecting property name enclosed in double quotes: line 1
#: column 2" vs "unexpected byte at 1"). The contract the desk shows is the
#: sentence before the colon; the tail is diagnostics, and freezing one
#: language's phrasing would be freezing an accident. The live-twin suite
#: makes the same cut (test_studio_scenes_rust asserts the prefix only).
CLIPPED_DETAIL = ("body_not_json",)


def _clip_detail(rec: dict[str, Any]) -> dict[str, Any]:
    body = rec.get("body")
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        head = str(body["error"]).split(":", 1)[0]
        rec = {**rec, "body": {**body, "error": head + ": <parser detail>"}}
    return rec


def dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(data))


def load(path: Path) -> dict[str, Any]:
    """Read one golden file, loudly.

    A missing file must never read as "nothing to check": an empty
    tests/golden/ would make the whole suite pass while proving nothing.
    """
    if not path.exists():
        raise AssertionError(
            f"missing golden {path} — regenerate with "
            "`.venv/bin/python tools/gen_golden.py` while the Python studio "
            "still exists, and commit the result"
        )
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data:
        raise AssertionError(f"golden {path} is empty — it proves nothing")
    return data


def launch(
    argv: list[str], box: Sandbox, env: dict[str, str]
) -> tuple[
    subprocess.Popen[bytes],
    int,
]:
    """Start one studio on a free port, sandboxed, and wait for it.

    free_port() closes the socket before the server binds it, so a busy
    machine can take the port in between; one retry on a fresh port is the
    same cheap answer studio_rust_case.py settled on.
    """
    for attempt in (0, 1):
        port = free_port()
        proc = subprocess.Popen(
            [*argv, str(port), "--localhost"],
            env=box.env(env),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_up(port)
            return proc, port
        except (AssertionError, OSError):
            proc.terminate()
            proc.wait(timeout=10)
            if attempt:
                raise
    raise AssertionError("unreachable")
