"""Which castle Castle Radio talks to — the app's pin, else the store's first.

The rule, in the order it is asked:

1. CASTLE_RADIO_HOST — the app's settings pin it (settings.json
   `castle_host`; runtime.rs and tools/desktop_env.py pass it). A pinned
   castle is not the store's to change, and "Find my castle" says so.
2. The first castle of the per-user store, CASTLE_DEVICES — where "Find my
   castle" writes (tools/castle_address.py) and the castle key lives, the
   same file the studio reads, so the desk and this page agree. Re-read
   whenever the file changes, so a castle found in one app is the castle in
   the other without a restart.
3. Nothing — the bridge says "No castle found yet" and dials nobody.

Only a store someone NAMED counts: a checkout run without CASTLE_DEVICES
would otherwise fall back to the repo's tracked devices.toml, which is the
seller's yard inventory — and the bridge has no address of its own
(docs/PRODUCTION-TODO.md §8). Both launchers name one.
"""

import os

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import castle_address
import hosts

PINNED = (
    "The app's settings name this castle (castle_host in settings.json) — "
    "change it there, or remove it to let Find my castle choose"
)
NO_STORE = (
    "This Castle Radio keeps no castle list (CASTLE_DEVICES is not set) — "
    "start it from the Castle Tools app, or set CASTLE_RADIO_HOST"
)
#: The host the store last gave, and the file's (mtime, size) when it did.
#: `follow` only ever replaces a HOST that is still exactly that answer, so
#: an address set any other way — a pin, a test — is never overruled.
_last: dict = {"host": None, "stamp": None}


def pinned():
    return bool(os.environ.get("CASTLE_RADIO_HOST"))


def has_store():
    return bool(os.environ.get("CASTLE_DEVICES"))


def _stamp():
    try:
        st = hosts.devices_path().stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _from_store():
    _last.update(host=castle_address.current(), stamp=_stamp())
    return _last["host"]


def initial():
    """The bridge's HOST at start-up."""
    if pinned():
        return os.environ["CASTLE_RADIO_HOST"]
    return _from_store() if has_store() else ""


def follow(host):
    """`host`, or the store's first castle when the store changed since it
    gave `host` — called before every request the bridge makes."""
    if pinned() or not has_store() or host != _last["host"]:
        return host
    return host if _stamp() == _last["stamp"] else _from_store()


def adopt(host, name=""):
    """Make `host` the store's first castle; the HOST the bridge now has."""
    if pinned():
        raise castle_address.ck.Refusal(PINNED, 409)
    if not has_store():
        raise castle_address.ck.Refusal(NO_STORE, 409)
    castle_address.adopt(host, name)
    return _from_store()
