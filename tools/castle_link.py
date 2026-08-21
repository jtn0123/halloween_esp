#!/usr/bin/env python3
"""The castle bridge: the Mac studio impersonates the castle to the desk.

The cue desk decides simulator-vs-device mode with one probe: does
/api/status answer from its own origin WITHOUT the {"studio": true} marker
(web/src/device.ts). Served from the castle's SD card that is naturally
true. Served from the studio on a laptop it never was — so scenes previewed
on the Mac stayed on the Mac.

This module closes that gap without touching the web code at all. The
studio answers /api/status with the CASTLE's own status when it can reach
one, and relays every castle-shaped /api/* request it does not itself own.
The desk then behaves exactly as it does on the porch: it mirrors scenes to
the hardware — while the audio keeps playing from the browser on the Mac.
Which is the wiring-day setup: lights on the castle, sound from the laptop,
no amplifiers required.

Who the castle is, in order:
  1. the CASTLE_HOST environment variable
  2. the first [entry] with a host= in devices.toml
(mDNS is not an option on the ESP32-S2 — see HARDWARE_FINDINGS.md.)
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import castle_native

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "devices.toml"

#: One WiFi round-trip to a busy ESP32. Long enough for a slow answer,
#: short enough that a dead castle cannot wedge the desk's own probe
#: (device.ts gives the whole thing 1500 ms — the cached path is what
#: usually answers inside that).
TIMEOUT_S = 2.0

#: The desk polls status continuously; the castle should not pay for every
#: poll. Fresh enough that "PLAYING" in the chip tracks reality.
_STATUS_TTL_S = 1.5

#: How long a dead castle stays presumed dead. Without this, every status
#: poll from a castle-less desk pays the full connect timeout.
_DOWN_TTL_S = 3.0

#: Keyed store rather than a bare module global, so refreshing it is a
#: mutation and not a rebind (ruff PLW0603).
_cache: dict[str, tuple[float, dict]] = {}


def castle_host() -> str | None:
    """The castle's address, or None when nothing is configured."""
    env = os.environ.get("CASTLE_HOST")
    if env is not None:
        # Set-but-empty means "explicitly no castle" — the e2e suite uses it
        # so a live device on the LAN cannot flip test expectations.
        return env or None
    try:
        doc = tomllib.loads(DEVICES.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    for entry in doc.values():
        if isinstance(entry, dict) and entry.get("host"):
            return str(entry["host"])
    return None


def status() -> dict | None:
    """The castle's /api/status, briefly cached; None if unreachable.

    The `bridged` key is added so a human reading the JSON can tell a
    relayed answer from a direct one; the desk ignores unknown keys.
    """
    hit = _cache.get("status")
    if hit and time.monotonic() - hit[0] < _STATUS_TTL_S:
        return hit[1]
    down = _cache.get("down")
    if down and time.monotonic() - down[0] < _DOWN_TTL_S:
        return None
    host = castle_host()
    if host is None:
        return None
    try:
        with urllib.request.urlopen(
                f"http://{host}/api/status", timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        # No HTTP server is what the flash build looks like — try the
        # native API before declaring the castle down.
        data = castle_native.status(host)
    if not isinstance(data, dict):
        _cache["down"] = (time.monotonic(), {})
        return None
    _cache.pop("down", None)
    data["bridged"] = host
    _cache["status"] = (time.monotonic(), data)
    return data


def forward(method: str, path_and_query: str,
            body: bytes = b"") -> tuple[int, bytes, str]:
    """Relay one request to the castle verbatim.

    Returns (status code, body, content-type). A castle error page comes
    back as-is — the desk's toasts already know how to say "failed" — and
    an unreachable castle is a 502, not an exception.
    """
    host = castle_host()
    if host is None:
        return 502, b'{"error": "no castle configured"}', "application/json"
    req = urllib.request.Request(f"http://{host}{path_and_query}",
                                 data=body or None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return (r.status or 200, r.read(),
                    r.headers.get("Content-Type") or "application/json")
    except urllib.error.HTTPError as e:
        return (e.code, e.read(),
                e.headers.get("Content-Type") or "application/json")
    except OSError:
        if _forward_native(host, method, path_and_query):
            return 200, b'{"queued":true}', "application/json"
        return 502, b'{"error": "castle not reachable"}', "application/json"


def _forward_native(host: str, method: str, path_and_query: str) -> bool:
    """The flash build's translation of the desk's three POST verbs."""
    if method != "POST":
        return False
    parts = urlsplit(path_and_query)
    q = parse_qs(parts.query)
    if parts.path == "/api/scene":
        sid = (q.get("s") or [""])[0]
        return bool(sid) and castle_native.scene(host, sid)
    if parts.path == "/api/stop":
        return castle_native.stop(host)
    if parts.path == "/api/volume":
        try:
            return castle_native.volume(host, int((q.get("v") or [""])[0]))
        except ValueError:
            return False
    return False
