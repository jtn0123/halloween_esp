"""The light-show lab's server: the comparison directory, plus a place for notes.

    .venv/bin/python demo/castle-radio/lab_server.py [--port 8894] [--bind 127.0.0.1]

`python -m http.server` served the lab until the phone needed to say
something back. Watching on a phone, a flag — "boring here", "love this" — is
worth more with its song time attached than a sentence typed afterwards, and
the page can only keep a note in the phone's own browser unless something on
the Mac will take it. So this is http.server's static handler with exactly one
thing it can write: `POST /notes` appends one JSON line to `notes.jsonl` in the
served directory, and `GET /notes.jsonl` hands them back, so a flag made on
the phone shows on the Mac's timeline too.

A note is small, typed and whitelisted field by field; nothing in a request
names a path. `show_lab.py --notes` prints them for the next pass.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / ".radio-data" / "comparison"
NOTES = "notes.jsonl"
MAX_BODY = 4096
TAGS = frozenset(("love", "boring", "busy", "late", "colour", "dim", "bright", "other"))
ABOUT = frozenset(("current", "candidate", "both"))
_TEXT = {"song": 80, "name": 120, "current": 40, "candidate": 40,
         "section": 120, "firmware": 16, "text": 500}  # fmt: skip
_write = Lock()


def clean(raw: Any) -> dict[str, Any] | None:
    """The note as it will be stored, or None when it is not one."""
    if not isinstance(raw, dict):
        return None
    t, tags, about = raw.get("t"), raw.get("tags"), raw.get("about")
    if not isinstance(t, (int, float)) or not 0 <= t < 3_600_000:
        return None
    if not isinstance(tags, list) or not set(tags) <= TAGS or about not in ABOUT:
        return None
    note: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "t": int(t),
        "tags": sorted(set(tags)),
        "about": about,
        "soften": raw.get("soften") is True,
    }
    for key, limit in _TEXT.items():
        value = raw.get(key, "")
        if not isinstance(value, str):
            return None
        note[key] = value.strip()[:limit]
    if not note["tags"] and not note["text"]:
        return None  # an empty flag says nothing
    return note


class LabHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        # No flag yet is an empty list, not a missing file: a 404 here was a
        # red error in the console of every page load before the first flag.
        if self.path == "/" + NOTES and not (Path(self.directory) / NOTES).exists():
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/notes":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BODY:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            note = clean(json.loads(self.rfile.read(size)))
        except ValueError:
            note = None
        if note is None:
            self.send_error(HTTPStatus.BAD_REQUEST, "not a note")
            return
        with _write, (Path(self.directory) / NOTES).open("a", encoding="utf-8") as out:
            out.write(json.dumps(note) + "\n")
        body = json.dumps(note).encode()
        self.send_response(HTTPStatus.CREATED)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        # The page and its notes change under a phone that keeps tabs open.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def server(directory: Path, port: int, bind: str) -> ThreadingHTTPServer:
    handler = partial(LabHandler, directory=str(directory))
    return ThreadingHTTPServer((bind, port), handler)


def fmt(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def report(path: Path) -> list[str]:
    """The notes, song by song in song-time order, for reading back."""
    if not path.is_file():
        return ["no notes yet"]
    notes = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    out: list[str] = []
    for song in sorted({n["name"] or n["song"] for n in notes}):
        out.append(song)
        for n in sorted(
            (n for n in notes if (n["name"] or n["song"]) == song),
            key=lambda n: n["t"],
        ):
            show = {"current": n["current"], "candidate": n["candidate"]}.get(
                n["about"], f"{n['current']} + {n['candidate']}"
            )
            said = " ".join(f"[{t}]" for t in n["tags"])
            text = f' "{n["text"]}"' if n["text"] else ""
            where = f"  ({n['section']})" if n["section"] else ""
            out.append(f"  {fmt(n['t']):>6}  {show:<22} {said}{text}{where}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8894)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with server(OUTPUT, args.port, args.bind) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
