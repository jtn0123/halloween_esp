"""Which castle are we talking to? One resolver for every tool.

Order: explicit argument (an IP, or a name from devices.toml) — CASTLE_HOST —
first entry in devices.toml. `candidates()` is the ordered list (fallbacks
included) that castle_link walks; `resolve()` is its first entry. Written because "10.27.27.7" was hardcoded in
three tools and one muscle memory, which is exactly one router-reshuffle away
from being wrong everywhere at once.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import TypedDict

DEVICES = Path(__file__).resolve().parent.parent / "devices.toml"

_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class _Device(TypedDict):
    """One devices.toml entry, normalized: the host plus its fallbacks."""

    host: str
    fallbacks: list[str]


def _table() -> dict[str, str]:
    return {name: cfg["host"] for name, cfg in _entries().items()}


def _entries() -> dict[str, _Device]:
    """devices.toml's device tables: name -> {host, fallbacks}. Missing or
    malformed file means no devices, not a traceback — the studio runs
    castle-less by design."""
    try:
        doc = tomllib.loads(DEVICES.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, _Device] = {}
    for name, cfg in doc.items():
        if isinstance(cfg, dict) and cfg.get("host"):
            out[name] = {
                "host": str(cfg["host"]),
                "fallbacks": [str(h) for h in cfg.get("fallbacks") or []],
            }
    return out


def candidates(arg: str | None = None) -> list[str]:
    """Every address worth trying, best first. Empty means "no castle".

    Order: the explicit argument (an IP, a host:port, or a devices.toml
    name, expanded to its host + fallbacks) — CASTLE_HOST (a comma list; a
    bare name is looked up, anything else passes through) — every
    devices.toml entry's host followed by its `fallbacks`. CASTLE_HOST
    set-but-EMPTY is "explicitly no castle": the e2e suite uses it so a
    live device on the LAN cannot flip test expectations.
    """
    entries = _entries()

    def expand(h: str) -> list[str]:
        e = entries.get(h)
        return [e["host"], *e["fallbacks"]] if e else [h]

    if arg:
        return expand(arg)
    env = os.environ.get("CASTLE_HOST")
    if env is not None:
        out: list[str] = []
        for h in (x.strip() for x in env.split(",")):
            if h:
                out.extend(expand(h))
        return out
    return _from_table()


def _from_table() -> list[str]:
    return [h for e in _entries().values() for h in (e["host"], *e["fallbacks"])]


def resolve(arg: str | None = None) -> str:
    """An IP to talk to, or a SystemExit that says how to provide one.

    `candidates()[0]` — except that an explicit name must exist in the
    table, and an empty CASTLE_HOST falls through to the table here (a CLI
    tool with no castle has nothing to do, so "none" is not an answer).
    """
    if arg and not _IP.match(arg) and arg not in _table():
        raise SystemExit(
            f"unknown device {arg!r} — not an IP and not in devices.toml "
            f"(known: {', '.join(_table()) or 'none'})"
        )
    found = candidates(arg) or _from_table()
    if found:
        return found[0]
    raise SystemExit(
        "no device given: pass an IP or name, set CASTLE_HOST, "
        "or add an entry to devices.toml"
    )


def maybe_host(argv: list[str]) -> tuple[str, list[str]]:
    """Pop a leading host/name from argv if present, else resolve a default.

    Lets `sd_sync.py status` work as well as `sd_sync.py 10.27.27.7 status`.
    """
    known_cmds = {
        "status",
        "ls",
        "purge",
        "push",
        "scenes",
        "rm",
        "play",
        "bootlog",
        "site",
        "ota",
        "health",
        "list",
        "press",
        "set",
        "watch",
    }
    if argv and (argv[0] in known_cmds):
        return resolve(None), argv
    if argv:
        return resolve(argv[0]), argv[1:]
    return resolve(None), argv


if __name__ == "__main__":
    print(resolve(sys.argv[1] if len(sys.argv) > 1 else None))
