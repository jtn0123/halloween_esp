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
#: sd_web_prefs.h kKeyMax — the longest key the firmware will hold.
KEY_MAX = 64

# A bare IP, or IP:port — the emulator chain publishes to 127.0.0.1:<port>,
# and refusing it here once broke the studio's auto-publish (B5 follow-up).
_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?$")


class _Device(TypedDict):
    """One devices.toml entry, normalized: the host plus its fallbacks."""

    host: str
    fallbacks: list[str]
    #: The castle key (firmware v5.74, sd_web_prefs.h) — "" for a castle
    #: that has none, which is every castle until its owner sets one.
    key: str


def _table() -> dict[str, str]:
    return {name: cfg["host"] for name, cfg in _entries().items()}


def devices_path() -> Path:
    """The inventory — and the castle-key store: CASTLE_DEVICES when it is
    set and not empty, else the repo's devices.toml. A packaged install
    points CASTLE_DEVICES at a per-user file (docs/notes/06-buyer-build.md
    "The castle key's one store"); core/src/studio_relay.rs and the
    `castle` bin read the same variable by the same rule."""
    env = os.environ.get("CASTLE_DEVICES", "")
    return Path(env) if env else DEVICES


def _entries(path: Path | None = None) -> dict[str, _Device]:
    """devices.toml's device tables: name -> {host, fallbacks, key}. Missing
    or malformed file means no devices, not a traceback — the studio runs
    castle-less by design. A key is a TOML string or nothing: castle-core's
    reader (core/src/hosts.rs) reads strings only, and the two must agree."""
    try:
        doc = tomllib.loads((path or devices_path()).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, _Device] = {}
    for name, cfg in doc.items():
        if isinstance(cfg, dict) and cfg.get("host"):
            key = cfg.get("key")
            out[name] = {
                "host": str(cfg["host"]),
                "fallbacks": [str(h) for h in cfg.get("fallbacks") or []],
                "key": key if isinstance(key, str) else "",
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


def valid_key(key: str) -> bool:
    """A key the firmware could hold (sd_web_prefs.h key_chars_ok): 1-64
    printable ASCII characters, no space. Anything else is never sent — a
    castle could not have it, and a newline in a header is an injection."""
    return 0 < len(key) <= KEY_MAX and all("!" <= c <= "~" for c in key)


def castle_key(host: str | None = None) -> str:
    """The castle key to send, or "" to send none.

    CASTLE_KEY wins when it is set (set-but-empty is "no key", the same
    convention CASTLE_HOST has); else the `key` of the FIRST devices.toml
    entry whose host or fallbacks name `host`; else the first entry's when
    no host is given. A key the firmware could not hold (`valid_key`) is
    no key. A castle with no key ignores the header, so sending one to the
    wrong castle costs nothing but a 401 from a castle that has a different
    one — which is the answer that should come back.

    castle-core's `hosts::castle_key` is the same rule for the studio's
    relay and the `castle` bin; tests/test_castle_key_rust.py holds the
    header each one SENDS to this function's answer (docs/PARITY.md).
    """
    env = os.environ.get("CASTLE_KEY")
    if env is not None:
        key = env.strip()
        return key if valid_key(key) else ""
    return stored_key(host)


def stored_key(host: str | None = None, path: Path | None = None) -> str:
    """castle_key's file half — what devices.toml (or `path`) holds for
    `host`, CASTLE_KEY not consulted. tools/castle_keys.py reads its own
    writes back through this before it replaces the file."""
    key = next(
        (
            e["key"]
            for e in _entries(path).values()
            if host is None or host in (e["host"], *e["fallbacks"])
        ),
        "",
    )
    return key if valid_key(key) else ""


def key_headers(host: str | None = None) -> dict[str, str]:
    """{"X-Castle-Key": key} for a castle with a key configured, else {}."""
    k = castle_key(host)
    return {"X-Castle-Key": k} if k else {}


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
