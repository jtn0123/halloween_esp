"""Where the castle is — devices.toml's address writer, castle_keys.py's twin.

The castle key and the castle's address live in one store, the file
hosts.py reads (`hosts.devices_path()`: CASTLE_DEVICES, else the repo's
devices.toml). tools/castle_keys.py writes a castle's KEY there; this writes
its ADDRESS, under the same guard (`castle_keys._store_file`: a file named
devices.toml in a folder that exists) and by the same discipline — line-level
edits that keep the owner's comments, every result read back through
hosts.py before it replaces the file.

`adopt(host, name)` is what "Find my castle" does with the castle the owner
picked (tools/castle_find.py browses for it; Castle Radio's Your castle page
and `castle_address.py adopt` call this). It makes that castle the store's
FIRST table — the castle `hosts.first_castle()` names and every client tries
first — with `host` its address and the mDNS `name` (castle-a1b2c3.local)
its first fallback, so a lease that moves is a name away from found again:

- a table already naming the castle (by `name` first — the stable identity —
  then by `host`) is updated where it stands and moved to the top, its `key`
  line and comments with it; an old IP literal that was its host is dropped
  (a lease that moved is a timeout, not a fallback), a name never is;
- otherwise a new table goes on top, marked with MARK.

A table castle_keys.py added just to hold a key carries castle_keys.MARK,
which `forget` reads as "take this table away whole"; adopting one swaps that
mark for this module's, so forgetting the key afterwards leaves the address.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import castle_keys as ck
import hosts

#: The first line of a table this module added.
MARK = "# Added by tools/castle_address.py — the castle Find my castle chose."
NEW_FILE = (
    "# The castle this computer knows: its address (tools/castle_address.py)\n"
    "# and its key (tools/castle_keys.py), one table per castle, read by\n"
    "# tools/hosts.py and castle-core's hosts.rs.\n"
)
_HOST = ck._HOST
_FALLBACKS = re.compile(r"^\s*fallbacks\s*=")
_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?$")
_BARE = re.compile(r"^[A-Za-z0-9_-]{1,63}$")


def current(path: Path | None = None) -> str:
    """The address the store's first castle has, "" for an empty store."""
    return next(iter(hosts.first_castle(path)), "")


def _owner(doc: dict[str, object], host: str, name: str) -> str | None:
    """The first table naming `name`, else the first naming `host`."""
    for want in (name, host):
        if want and (found := ck._owner(doc, want)) is not None:
            return found
    return None


def _old(cfg: object) -> tuple[str, list[str]]:
    """A table's (host, fallbacks) as tomllib read them."""
    if not isinstance(cfg, dict):
        return "", []
    fallbacks = cfg.get("fallbacks")
    kept = [str(h) for h in fallbacks] if isinstance(fallbacks, list) else []
    return str(cfg.get("host") or ""), kept


def _fallbacks(was: str, kept: list[str], host: str, name: str) -> list[str]:
    """The name first, then what the table had — less the new host and an
    old IP literal host."""
    out: list[str] = []
    for h in (name, *([] if _IP.match(was) else [was]), *kept):
        if h and h != host and h not in out:
            out.append(h)
    return out


def _with_comment(new: str, line: str) -> str:
    """`new` carrying the owner's trailing comment from the line it replaces
    (a host or a name never holds a '#', so the first one starts it)."""
    _, sep, note = line.partition("#")
    return f"{new}   #{note}" if sep else new


def _rewrite(block: list[str], host: str, fallbacks: list[str] | None) -> list[str]:
    """`block` (one table, header first) with its host line replaced and,
    unless `fallbacks` is None (unchanged), its fallbacks — a multi-line
    array whole — each where it stood; missing ones go after the header."""
    out: list[str] = []
    open_array = False
    host_at = -1
    names = (
        f"fallbacks = [{', '.join(ck.toml_string(f) for f in fallbacks)}]"
        if fallbacks
        else None
    )
    for line in block:
        code = line.split("#", 1)[0]
        if open_array:
            open_array = "]" not in code
            continue
        if host_at < 0 and _HOST.match(line):
            host_at = len(out)
            out.append(_with_comment(f"host = {ck.toml_string(host)}", line))
            continue
        if fallbacks is not None and _FALLBACKS.match(line):
            value = code.partition("=")[2]
            open_array = "[" in value and "]" not in value
            if names:
                out.append(names if open_array else _with_comment(names, line))
                names = None
            continue
        out.append(line)
    if host_at < 0:
        host_at = 1
        out.insert(host_at, f"host = {ck.toml_string(host)}")
    if names:
        out.insert(host_at + 1, names)
    return out


def _fresh_name(doc: dict[str, object], name: str) -> str:
    stem = name.removesuffix(".local")
    if _BARE.match(stem) and stem not in doc:
        return stem
    return next(f"castle-{n}" for n in range(1, 1000) if f"castle-{n}" not in doc)


def _comments_above(lines: list[str], at: int) -> int:
    """Where the comment lines that touch line `at` from above begin — the
    note an owner wrote over a table travels with it. A run that reaches the
    top of the file is the file's own header, and stays."""
    start = at
    while start and lines[start - 1].strip().startswith("#"):
        start -= 1
    return at if start == 0 else start


def _edit(text: str, doc: dict[str, object], host: str, name: str) -> str:
    lines = text.splitlines()
    owner = _owner(doc, host, name)
    if owner is None:
        fresh = _fresh_name(doc, name)
        block = [f"[{fresh}]", MARK, f"host = {ck.toml_string(host)}"]
        if name and name != host:
            block.append(f"fallbacks = [{ck.toml_string(name)}]")
        rest = lines
    else:
        found = next((t for t in ck._tables(lines) if t[0] == owner), None)
        if found is None:
            raise ValueError(f"cannot find the [{owner}] table to edit")
        _, head, end = found
        was, kept = _old(doc[owner])
        fallbacks = _fallbacks(was, kept, host, name)
        start = _comments_above(lines, head)
        block = lines[start:end]
        body = _rewrite(
            block[head - start :], host, None if fallbacks == kept else fallbacks
        )
        block = block[: head - start] + [
            MARK if line.strip() == ck.MARK else line for line in body
        ]
        while block and not block[-1].strip():
            block.pop()
        cut = start - 1 if start and not lines[start - 1].strip() else start
        rest = lines[:cut] + lines[end:]
    heads = ck._tables(rest)
    top = _comments_above(rest, heads[0][1]) if heads else len(rest)
    lead = [""] if top and rest[top - 1].strip() else []
    gap = [""] if heads else []
    return "\n".join([*rest[:top], *lead, *block, *gap, *rest[top:]]) + "\n"


def _check(
    probe: Path, host: str, name: str, key: str, others: dict[str, object]
) -> None:
    """Read the probe back the way every client will, before it is kept: the
    castle first, its key where it was, every other table as it was."""
    first = hosts.first_castle(probe)
    good = bool(first) and first[0] == host and (not name or name in first)
    good = good and hosts.stored_key(host, probe) == key
    doc = tomllib.loads(probe.read_text(encoding="utf-8"))
    rest = dict(list(doc.items())[1:])
    if not good or rest != others:
        raise ValueError(f"{ck.STORE_NAME} could not be updated safely")


def adopt(host: str, name: str = "", path: Path | None = None) -> bool:
    """Make the castle at `host` (mDNS `name`, when known) the store's first.
    True when the file changed; False when it already said exactly that."""
    ck._check_host(host)
    if name:
        ck._check_host(name)
    store = ck._store_file(path or hosts.devices_path())
    text, doc = ck._read(store)
    if text == ck.NEW_FILE or not text.strip():
        text = NEW_FILE
    owner = _owner(doc, host, name)
    cfg = doc.get(owner) if owner else None
    key = str(cfg.get("key") or "") if isinstance(cfg, dict) else ""
    others = {k: v for k, v in doc.items() if k != owner}
    new = _edit(text, doc, host, name)
    if new == text and store.exists():
        return False
    fd, probe_name = tempfile.mkstemp(
        prefix=f".{ck.STORE_NAME}.", suffix=".tmp", dir=store.parent
    )
    probe = Path(probe_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(new)
        mode = store.stat().st_mode & 0o777 if store.exists() else 0o600
        os.chmod(probe, mode)
        _check(probe, host, name, key if hosts.valid_key(key) else "", others)
        os.replace(probe, store)
    finally:
        probe.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["show"]:
            print(current() or "no castle yet")
            return 0
        if len(args) in (2, 3) and args[0] == "adopt":
            changed = adopt(args[1], args[2] if len(args) == 3 else "")
            print("castle saved" if changed else "already the castle")
            return 0
    except (OSError, ValueError) as e:
        print(f"castle address not saved: {e}", file=sys.stderr)
        return 1
    print(
        "usage: castle_address.py adopt <host> [<name>.local] | show", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
