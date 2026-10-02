"""The castle key store's one writer — devices.toml, the file hosts.py reads.

Firmware v5.74 lets an owner lock a castle's writes behind a key
(firmware/sd_web_prefs.h). Every client that sends one reads it from the
same place by the same rule (hosts.castle_key, core/src/hosts.rs): CASTLE_KEY
when it is set, else the `key` of the devices.toml entry whose host or
fallbacks name the castle. The file is `hosts.devices_path()` — CASTLE_DEVICES
when set, else the repo's own devices.toml — so a dev checkout keeps the
inventory it always had and a packaged install points CASTLE_DEVICES at a
per-user file (docs/notes/06-buyer-build.md, "The castle key's one store").

This module is the only thing that WRITES a key there: Castle Radio imports
it, and the studio spawns it with the key on stdin (never argv — a process
list shows argv to every user on the machine):

    castle_keys.py remember <host>     key on stdin; first matching table
    castle_keys.py forget <host>
    castle_keys.py check-staged        the pre-commit guard (below)

Edits are line-level, so the file's comments survive: the matching table's
`key =` line is replaced, inserted after its `host =`, or removed. A castle
the file does not know gets a table of its own, marked, and `forget` takes a
marked table away whole — remembering a key must not leave a stray castle in
the inventory once the key is gone. Every write is checked by reading the
result back through hosts.py before it replaces the file, and nothing here
ever prints a key.

devices.toml is TRACKED and the repo is public, so `check-staged` refuses a
commit that stages a devices.toml carrying a key (githooks/pre-commit).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts

#: The first line of a table this module added, which `forget` removes whole.
MARK = "# Added by tools/castle_keys.py to remember this castle's key."
NEW_FILE = (
    "# Castle keys this computer remembers (tools/castle_keys.py), one table\n"
    "# per castle, read by tools/hosts.py and castle-core's hosts.rs.\n"
)
_HEADER = re.compile(r"^\s*\[\s*([^\[\]#]+?)\s*\]\s*(#.*)?$")
_KEY = re.compile(r"^\s*key\s*=")
_HOST = re.compile(r"^\s*host\s*=")
#: What a castle address may be: a name, an IPv4/IPv6 literal, a port.
_HOST_OK = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


def pinned() -> bool:
    """CASTLE_KEY is set: it wins over the file, so the file cannot follow."""
    return "CASTLE_KEY" in os.environ


def stored_key(host: str) -> str:
    """The key the file holds for `host` — CASTLE_KEY not consulted."""
    return hosts.stored_key(host)


def toml_string(s: str) -> str:
    """A TOML basic string. Keys and hosts are printable ASCII, so the
    quote and the backslash are the only characters that need escaping."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check_host(host: str) -> None:
    if not _HOST_OK.match(host):
        raise ValueError("not a castle address")


def _tables(lines: list[str]) -> list[tuple[str, int, int]]:
    """(name, header line, end) for each [table] — `end` is exclusive."""
    heads = [
        (i, m.group(1).strip().strip("\"'"))
        for i, line in enumerate(lines)
        if (m := _HEADER.match(line))
    ]
    return [
        (name, at, heads[n + 1][0] if n + 1 < len(heads) else len(lines))
        for n, (at, name) in enumerate(heads)
    ]


def _owner(doc: dict[str, object], host: str) -> str | None:
    """hosts.castle_key's match: the FIRST table naming `host`."""
    for name, cfg in doc.items():
        if isinstance(cfg, dict) and cfg.get("host"):
            names = (str(cfg["host"]), *(str(h) for h in cfg.get("fallbacks") or []))
            if host in names:
                return name
    return None


def _read(path: Path) -> tuple[str, dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return NEW_FILE, {}
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # Never rewrite a file this module cannot read: the inventory is the
        # owner's, and "fixed" by a writer that guessed is lost.
        raise ValueError(f"{path.name} is not valid TOML — fix it by hand") from None


def _edit(text: str, doc: dict[str, object], host: str, key: str | None) -> str:
    """`text` with `host`'s key set (`key`) or removed (None)."""
    lines = text.splitlines()
    name = _owner(doc, host)
    if name is None:
        if key is None:
            return text
        taken = set(doc)
        fresh = next(
            f"castle-{n}" for n in range(1, 1000) if f"castle-{n}" not in taken
        )
        tail = ["", f"[{fresh}]", MARK, f"host = {toml_string(host)}"]
        return "\n".join([*lines, *tail, f"key = {toml_string(key)}"]) + "\n"
    found = next((t for t in _tables(lines) if t[0] == name), None)
    if found is None:
        raise ValueError(f"cannot find the [{name}] table to edit")
    _, start, end = found
    block = lines[start:end]
    if key is None and MARK in (line.strip() for line in block):
        cut = start - 1 if start and not lines[start - 1].strip() else start
        return "\n".join(lines[:cut] + lines[end:]) + "\n"
    body = [line for line in block if not _KEY.match(line)]
    if key is not None:
        after = next((i for i, line in enumerate(body) if _HOST.match(line)), 0)
        body.insert(after + 1, f"key = {toml_string(key)}")
    return "\n".join(lines[:start] + body + lines[end:]) + "\n"


def _write(path: Path, host: str, key: str | None) -> None:
    _check_host(host)
    if key is not None and not hosts.valid_key(key):
        raise ValueError("a castle key is 1-64 printable characters, no spaces")
    text, doc = _read(path)
    new = _edit(text, doc, host, key)
    # Read back through the reader every client uses before the file is
    # touched: the edit is only good if hosts.py now answers what was meant.
    probe = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        probe.write_text(new, encoding="utf-8")
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        os.chmod(probe, mode)
        if hosts.stored_key(host, probe) != (key or ""):
            raise ValueError(f"{path.name} could not be updated safely")
        os.replace(probe, path)
    finally:
        probe.unlink(missing_ok=True)


def remember(host: str, key: str, path: Path | None = None) -> None:
    """Remember `key` for the castle at `host`."""
    _write(path or hosts.devices_path(), host, key)


def forget(host: str, path: Path | None = None) -> None:
    """Forget the key remembered for `host`; a no-op when there is none."""
    _write(path or hosts.devices_path(), host, None)


def holds_key(text: str) -> bool:
    """Whether a devices.toml text carries any non-empty key. A file that
    does not parse is judged line by line, erring towards "it does"."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return any(
            _KEY.match(line)
            and line.split("=", 1)[1].split("#")[0].strip() not in ('""', "''")
            for line in text.splitlines()
        )
    return any(isinstance(cfg, dict) and cfg.get("key") for cfg in doc.values())


def check_staged() -> int:
    """Refuse a commit that stages a devices.toml carrying a castle key."""
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    bad = []
    for name in (n for n in names if Path(n).name == "devices.toml"):
        staged = subprocess.run(
            ["git", "show", f":{name}"], capture_output=True, text=True, check=False
        ).stdout
        if holds_key(staged):
            bad.append(name)
    for name in bad:
        print(
            f"pre-commit: {name} is staged with a castle key in it — this repo is "
            "public. Unstage it (git restore --staged), or keep the key in a "
            "CASTLE_DEVICES file outside the repo.",
            file=sys.stderr,
        )
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["check-staged"]:
            return check_staged()
        if len(args) == 2 and args[0] == "remember":
            remember(args[1], sys.stdin.readline().rstrip("\r\n"))
            return 0
        if len(args) == 2 and args[0] == "forget":
            forget(args[1])
            return 0
    except (OSError, ValueError) as e:
        # The reason only ever names the file or the rule — never the key.
        print(f"castle key not saved: {e}", file=sys.stderr)
        return 1
    print(
        "usage: castle_keys.py remember <host> | forget <host> | check-staged",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
