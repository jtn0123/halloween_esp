"""The routes behind the "Update castle" card (castle-update.js), and the one
the desktop app's updater asks when its owner opted in to pre-releases.

    GET  /radio/castle/update   the castle's firmware, the newest on the
                                owner's channel for its board, and the
                                update job (the card polls this)
    POST /radio/castle/update   start the update: {"action": "update"}
    GET  /radio/app/release     which Castle Tools release the owner's
                                channel offers, and its latest.json
                                (desktop/src-tauri/src/channel.rs)

Never automatic: firmware goes to the castle only from the POST, and only
the card's button sends it. The work is tools/castle_update.py; this module
is the cache and the one job thread around it. GitHub allows 60
unauthenticated calls an hour, so what the channel offers is remembered for
LOOKUP_TTL and the POST reuses the offer the card showed. While an update
runs the GET answers from the job alone: a castle taking its firmware is
not asked anything else.

The channel is the owner's: CASTLE_PRERELEASE, which the desktop app sets
from its settings (tools/release_channel.py).
"""

import tempfile
import threading
import time
from pathlib import Path

import radio_env  # noqa: F401 — the sandbox, then tools/ on the path

# isort: split
import castle_update
import desktop_release as rel
import device_bridge  # the castle's address
import release_channel as channel
import request_guard

LOOKUP_TTL = 3600.0
BAD_POST = "Invalid update request"
_LOCK = threading.Lock()
_OFFERS: dict = {}  # (board, prerelease) -> (when, castle_update.Offer)
_APPS: dict = {}  # prerelease -> (when, rel.Release)
_JOB: dict = {"running": False, "done": False}


def _cached(table, key, look):
    """`look()`, remembered under `key` for LOOKUP_TTL. A failure is not
    remembered: the next ask tries GitHub again."""
    now = time.monotonic()
    with _LOCK:
        hit = table.get(key)
        if hit and now - hit[0] < LOOKUP_TTL:
            return hit[1]
    value = look()
    with _LOCK:
        table[key] = (now, value)
    return value


def offer_for(board, prerelease):
    def look():
        with tempfile.TemporaryDirectory(prefix="castle-fw-") as tmp:
            return castle_update.offer(rel.http_fetch, board, prerelease, Path(tmp))

    return _cached(_OFFERS, (board, prerelease), look)


def job():
    with _LOCK:
        return dict(_JOB)


def plan(host, prerelease):
    """What the card shows: the castle measured against its offer."""
    st = castle_update.castle_status(host)
    board = castle_update.identity(st)[1]
    found = castle_update.judge(st, offer_for(board, prerelease))
    return {
        "current": found.current,
        "board": found.board,
        "variant": found.variant,
        "available": found.available,
        "tag": found.tag,
        "update": found.update,
        "message": found.message,
    }


def _work(host, prerelease):
    def phase(line):
        with _LOCK:
            _JOB["phase"] = line

    outcome: dict[str, str | None] = {
        "error": "The update stopped before it finished — see the log."
    }
    try:
        result = castle_update.run(
            host,
            None,
            prerelease,
            phase,
            lambda board: offer_for(board, prerelease),
            castle_update.TRIES,
            castle_update.EVERY,
        )
        outcome = {"result": result, "error": None}
    except castle_update.UpdateError as exc:
        outcome = {"error": str(exc)}
    except (OSError, ValueError) as exc:
        outcome = {"error": f"The update stopped: {exc}"}
    finally:
        with _LOCK:
            _JOB.update(running=False, done=True, phase="", **outcome)


def start(host, prerelease):
    with _LOCK:
        if _JOB.get("running"):
            raise request_guard.Refused("The castle is already being updated.", 409)
        _JOB.clear()
        _JOB.update(running=True, done=False, phase="Starting", error=None)
        _JOB["started"] = time.time()
    threading.Thread(
        target=_work, args=(host, prerelease), daemon=True, name="castle-update"
    ).start()
    return job()


def get_update(handler, _parsed):
    now = job()
    if now.get("running"):
        handler.reply({"job": now})
        return
    try:
        handler.reply({**plan(device_bridge.castle(), channel.opted_in()), "job": now})
    except (castle_update.UpdateError, OSError) as exc:
        handler.reply({"error": str(exc), "job": now}, 502)


def post_update(handler):
    """The button: `{"action": "update"}` as JSON, like every POST route."""
    if handler.json_body(BAD_POST).get("action") != "update":
        raise ValueError(BAD_POST)
    handler.reply({"job": start(device_bridge.castle(), channel.opted_in())}, 202)


def get_app_release(handler, _parsed):
    early = channel.opted_in()
    try:
        found = _cached(_APPS, early, lambda: channel.newest(rel.http_fetch, early))
    except rel.ReleaseError as exc:
        handler.reply({"error": str(exc)}, 502)
        return
    handler.reply(
        {
            "tag": found.tag,
            "latest_json": channel.app_manifest(found),
            "prerelease": early,
        }
    )
