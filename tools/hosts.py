"""Which castle are we talking to? One resolver for every tool.

Order: explicit argument (an IP, or a name from devices.toml) — CASTLE_HOST —
first entry in devices.toml. Written because "10.27.27.7" was hardcoded in
three tools and one muscle memory, which is exactly one router-reshuffle away
from being wrong everywhere at once.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

DEVICES = Path(__file__).resolve().parent.parent / "devices.toml"

_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _table() -> dict[str, str]:
    if not DEVICES.exists():
        return {}
    doc = tomllib.loads(DEVICES.read_text())
    return {name: str(cfg.get("host", "")) for name, cfg in doc.items()
            if isinstance(cfg, dict) and cfg.get("host")}


def resolve(arg: str | None = None) -> str:
    """An IP to talk to, or a SystemExit that says how to provide one."""
    table = _table()
    if arg:
        if _IP.match(arg):
            return arg
        if arg in table:
            return table[arg]
        raise SystemExit(f"unknown device {arg!r} — not an IP and not in devices.toml "
                         f"(known: {', '.join(table) or 'none'})")
    env = os.environ.get("CASTLE_HOST", "")
    if env:
        return env if _IP.match(env) else table.get(env, env)
    if table:
        return next(iter(table.values()))
    raise SystemExit("no device given: pass an IP or name, set CASTLE_HOST, "
                     "or add an entry to devices.toml")


def maybe_host(argv: list[str]) -> tuple[str, list[str]]:
    """Pop a leading host/name from argv if present, else resolve a default.

    Lets `sd_sync.py status` work as well as `sd_sync.py 10.27.27.7 status`.
    """
    known_cmds = {"status", "ls", "purge", "push", "scenes", "rm", "play",
                  "bootlog", "site", "ota", "health", "list", "press", "set",
                  "watch"}
    if argv and (argv[0] in known_cmds):
        return resolve(None), argv
    if argv:
        return resolve(argv[0]), argv[1:]
    return resolve(None), argv


if __name__ == "__main__":
    print(resolve(sys.argv[1] if len(sys.argv) > 1 else None))
