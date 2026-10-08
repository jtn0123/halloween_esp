"""One honest HTTP exchange with a castle, for the tools that WATCH one.

tools/soak.py (the 72-hour run) and tools/power_cycle.py (the cold-boot
torture) both need the castle's own answer every time they ask — nothing
cached, nothing retried behind their back, and a castle that does not answer
reported as exactly that, the moment it happens. castle_link.py is the
studio's bridge and does the opposite on purpose (a desk polls it every
second and the castle should not pay for each poll), so it is not this.

The castle is named the way every tool names it (tools/hosts.py): an IP, a
host:port (the emulator), or an mDNS name. The castle key, when one is
configured, rides along as hosts.key_headers says; the routes these tools
use — status, health, events, bootlog, play/scene/stop/show — are open on a
keyed castle anyway (firmware/sd_web_prefs.h), so a missing key costs
nothing here.

The reset reason is /api/health's `last_reset` and `was_crash`
(castle_health.h reason_str), carried since long before the buyer build.
v5.75 did not add a second field for it: it put the same word on the
owner's page, and taught was_crash() two more crashes (`power-glitch`,
`cpu-lockup`). A castle that does not report it answers "", which every
caller reports as "not reported".
"""

from __future__ import annotations

import http.client
import json
import urllib.parse

import hosts

#: castle_health.h reason_str() for the resets was_crash() counts — the
#: firmware fell over, rather than someone switching it off or flashing it.
#: Compared case-insensitively: the C spells PANIC and BROWNOUT in capitals.
#: The last two since v5.75, whose was_crash() says so itself anyway.
CRASH_RESETS = frozenset(
    {
        "panic",
        "int-watchdog",
        "task-watchdog",
        "watchdog",
        "brownout",
        "power-glitch",
        "cpu-lockup",
    }
)

#: How long one exchange may take. A busy ESP32 on Wi-Fi answers status in
#: tens of milliseconds and a full event ring in a few hundred; five seconds
#: is a castle that is not answering, not a slow one.
TIMEOUT_S = 5.0


class Unreachable(OSError):
    """Nothing answered: refused, timed out, reset, or the name did not
    resolve. The one failure a monitor counts as the castle being gone."""


def split_host(host: str) -> tuple[str, int]:
    """("10.0.0.7", 80) from "10.0.0.7"; the port when one is given."""
    parts = urllib.parse.urlsplit("//" + host.strip())
    if not parts.hostname:
        raise ValueError(f"not a castle address: {host!r}")
    return parts.hostname, parts.port or 80


def request(
    host: str, method: str, path: str, timeout: float = TIMEOUT_S
) -> tuple[int, bytes]:
    """(status code, body) of one request, or Unreachable."""
    name, port = split_host(host)
    conn = http.client.HTTPConnection(name, port, timeout=timeout)
    headers = {"Connection": "close", **hosts.key_headers(host)}
    try:
        # A POST with no body still says so: the board's httpd waits for a
        # Content-Length on a POST, and http.client sends none for None.
        body = b"" if method in ("POST", "PUT") else None
        conn.request(method, path, body=body, headers=headers)
        reply = conn.getresponse()
        return reply.status, reply.read()
    except (OSError, http.client.HTTPException) as e:
        raise Unreachable(f"{host}{path}: {e or type(e).__name__}") from e
    finally:
        conn.close()


def get_json(host: str, path: str, timeout: float = TIMEOUT_S) -> object | None:
    """The parsed reply to a GET, or None when the castle answered with
    something else — a 404 from a firmware that predates the route, a 503,
    a body that is not JSON. Unreachable when nothing answered at all."""
    code, body = request(host, "GET", path, timeout)
    if code != 200:
        return None
    try:
        got: object = json.loads(body)
    except ValueError:
        return None
    return got


def get_dict(host: str, path: str, timeout: float = TIMEOUT_S) -> dict | None:
    """get_json for the replies that are objects (status, health)."""
    got = get_json(host, path, timeout)
    return got if isinstance(got, dict) else None


def post(host: str, path: str, timeout: float = TIMEOUT_S) -> int:
    """POST a show command; the status code (200 = queued)."""
    return request(host, "POST", path, timeout)[0]


def reset_reason(health: dict | None) -> str:
    """Why the castle last started: /api/health's `last_reset`, else ""."""
    got = (health or {}).get("last_reset")
    return got if isinstance(got, str) else ""


def is_crash(reason: str, health: dict | None = None) -> bool:
    """Did that start follow a crash? The castle's own `was_crash` when it
    says so, else the reason's own word."""
    if (health or {}).get("was_crash") is True:
        return True
    return reason.strip().lower() in CRASH_RESETS


def as_int(reply: dict | None, key: str) -> int | None:
    """An integer field, or None when the firmware does not report it. A
    bool is not an integer here, whatever Python thinks."""
    got = (reply or {}).get(key)
    return got if isinstance(got, int) and not isinstance(got, bool) else None


def scene_ids(status: dict | None) -> list[str]:
    """The castle's playable scenes, from /api/status `scenes` (v5.42):
    every id but the two control words."""
    raw = (status or {}).get("scenes")
    ids = raw.split(",") if isinstance(raw, str) else []
    return [s for s in (x.strip() for x in ids) if s and s not in ("stop", "halt")]
