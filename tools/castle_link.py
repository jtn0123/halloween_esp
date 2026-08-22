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

import http.client
import json
import time
from urllib.parse import parse_qs, urlsplit

import castle_native
import hosts as hosts_mod

#: devices.toml lives behind hosts.py now (hosts.DEVICES); patch it there.

#: One WiFi round-trip to a busy ESP32: the read budget for the quick verbs
#: (status, the POSTs). Long enough for a slow answer, short enough that a
#: wedged castle cannot hold the desk's own probe hostage.
#: What the castle answers with, and what we answer for it when it cannot.
JSON_MIME = "application/json"
TIMEOUT_S = 2.0

#: Connecting to a LAN address takes milliseconds or never happens — a dead
#: IP costs the whole value PER HOST, so the probe keeps it short (the desk
#: gives its own probe 2500 ms and re-probes while it waits; device.ts).
PROBE_CONNECT_S = 1.0

#: Per-verb READ budgets. A 2.4 MB PUT at WiFi-to-SD speed holds the
#: castle's single httpd task for many seconds, and it acks only after the
#: last byte hit the card — the 2 s that fits a POST reported every large
#: send as "castle not reachable" (pass 1, J1-8).
READ_BUDGET_S = {"PUT": 60.0, "DELETE": 30.0, "SD_GET": 60.0,
                 "FILES_GET": 5.0}

#: The desk polls status continuously; the castle should not pay for every
#: poll. Fresh enough that "PLAYING" in the chip tracks reality.
_STATUS_TTL_S = 1.5

#: How long a dead castle stays presumed dead. Without this, every status
#: poll from a castle-less desk pays the full connect timeout.
_DOWN_TTL_S = 3.0

#: Keyed store rather than a bare module global, so refreshing it is a
#: mutation and not a rebind (ruff PLW0603).
_cache: dict[str, tuple[float, dict]] = {}


def castle_hosts() -> list[str]:
    """Every address worth trying, best first.

    The ordered list is hosts.candidates() — CASTLE_HOST (comma list; empty
    means no castle), else every devices.toml entry's `host` + `fallbacks`.
    Until the DHCP reservation exists, a router re-leasing the castle's IP
    silently killed the bridge — the fallback list is the belt to that
    suspender. Whichever host last answered is remembered and tried first.
    """
    hosts = hosts_mod.candidates()
    up = _cache.get("up")
    if up:
        h = str(up[1].get("host", ""))
        if h in hosts:
            hosts.remove(h)
            hosts.insert(0, h)
    return hosts


def native_host(host: str) -> bool:
    """Is the native leg worth trying for this address?

    The native API lives on 6053 whatever the HTTP port is; a host written
    WITH a port ("127.0.0.1:8093" — every emulator, every bench studio)
    names an HTTP server on purpose, and handing "host:port" to
    aioesphomeapi only bought a reconnect thread logging "Error resolving"
    every 5 s for the life of the studio (J2-8).
    """
    return ":" not in host


def castle_host() -> str | None:
    """The castle's current best address, or None when none is configured."""
    hosts = castle_hosts()
    return hosts[0] if hosts else None


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
    hosts = castle_hosts()
    if not hosts:
        return None
    data, good = None, hosts[0]
    for host in hosts:
        try:
            code, raw, _ = _call(host, "GET", "/api/status", b"",
                                 PROBE_CONNECT_S, TIMEOUT_S)
            if code == 200:
                data, good = json.loads(raw), host
                break
        except (OSError, http.client.HTTPException, ValueError):
            continue
    if not isinstance(data, dict) and native_host(hosts[0]):
        # No HTTP server is what the flash build looks like — try the
        # native API before declaring the castle down. Primary only: a
        # native _Link per dead fallback would leak reconnect threads.
        data = castle_native.status(hosts[0])
    if not isinstance(data, dict):
        _cache["down"] = (time.monotonic(), {})
        return None
    _cache.pop("down", None)
    _cache["up"] = (time.monotonic(), {"host": good})
    data["bridged"] = good
    _cache["status"] = (time.monotonic(), data)
    return data


class Unreachable(OSError):
    """Could not connect: the body never left — trying elsewhere is safe."""


class Stalled(OSError):
    """Connected, then no complete answer: the request MAY have landed."""


def _call(host: str, method: str, path: str, body: bytes,
          connect_s: float, read_s: float) -> tuple[int, bytes, str]:
    """One HTTP exchange with a connect budget and a separate read budget.

    urllib's single timeout governed every socket op, so the 2 s that suits
    a status poll also judged a multi-megabyte PUT. Raises Unreachable when
    the connect fails (nothing sent) and Stalled for anything after that.
    """
    conn = http.client.HTTPConnection(host, timeout=connect_s)
    try:
        try:
            conn.connect()
        except OSError as e:
            raise Unreachable(str(e)) from e
        try:
            if conn.sock is not None:
                conn.sock.settimeout(read_s)
            conn.request(method, path, body=body or None)
            r = conn.getresponse()
            return (r.status, r.read(),
                    r.getheader("Content-Type") or JSON_MIME)
        except (OSError, http.client.HTTPException) as e:
            raise Stalled(str(e)) from e
    finally:
        conn.close()


def _read_budget(method: str, path: str) -> float:
    if method == "PUT":
        return READ_BUDGET_S["PUT"]
    if method == "DELETE":
        return READ_BUDGET_S["DELETE"]
    if method == "GET" and path.startswith("/sd/"):
        return READ_BUDGET_S["SD_GET"]
    if method == "GET" and path.startswith("/api/files"):
        return READ_BUDGET_S["FILES_GET"]
    return TIMEOUT_S


def forward(method: str, path_and_query: str,
            body: bytes = b"") -> tuple[int, bytes, str]:
    """Relay one request to the castle verbatim.

    Returns (status code, body, content-type). A castle error page comes
    back as-is — the desk's toasts already know how to say "failed" — and
    an unreachable castle is a 502, not an exception. A castle that took
    the request and then went quiet is a 504: the bytes may have landed,
    so they are NOT replayed to the next host (a PUT re-sent in full to a
    fallback address of the same castle is the pass-1 J1-8 finding).
    """
    hosts = castle_hosts()
    if not hosts:
        return 502, b'{"error": "no castle configured"}', JSON_MIME
    read_s = _read_budget(method, path_and_query)
    for host in hosts:
        try:
            code, out, ctype = _call(host, method, path_and_query, body,
                                     TIMEOUT_S, read_s)
        except Unreachable:
            continue
        except Stalled:
            if method == "GET":
                continue       # nothing changed anywhere; the next host may do
            return (504, json.dumps({"error": "castle took the request but "
                    "did not answer in time — it may have landed; check "
                    "before sending again"}).encode(), JSON_MIME)
        # The castle ANSWERED — its verdict stands, error or not — and an
        # answer of ANY kind proves it is up: a probe that failed while it
        # was rebooting must not keep reporting it dead for 3 s after a
        # click it plainly served (J2-4).
        _cache.pop("down", None)
        if 200 <= code < 300:
            _cache["up"] = (time.monotonic(), {"host": host})
            if method != "GET":
                # The desk re-polls ~1 s after a click; a status cached
                # BEFORE the click would hand it the old world (J1-5).
                _cache.pop("status", None)
        return code, out, ctype
    if _forward_native(hosts[0], method, path_and_query):
        _cache.pop("status", None)
        _cache.pop("down", None)
        return 200, b'{"queued":true}', JSON_MIME
    return 502, b'{"error": "castle not reachable"}', JSON_MIME


def _forward_native(host: str, method: str, path_and_query: str) -> bool:
    """The flash build's translation of the desk's three POST verbs.

    Only with a live native session: a dead castle must 502 for EVERY verb,
    and the stubs below cannot tell "nothing to press" from "pressed".
    """
    if method != "POST" or not native_host(host):
        return False
    if not castle_native.connected(host):
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
