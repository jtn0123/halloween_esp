"""Native-API leg of the castle bridge, for builds that serve no HTTP.

castle_link speaks HTTP first because the SD build answers it. The
all-in-flash build exposes only ESPHome's native API (port 6053) — so when
port 80 is closed, castle_link hands the same four desk verbs to this
module instead: status, scene, stop, volume. Each maps onto entities the
flash firmware already has (the scene__* buttons, the media player, the
current_scene/current_track text sensors), so the firmware needs nothing
added — and the OTA slot budget stays untouched.

One daemon thread owns an asyncio loop and a persistent connection that
reconnects by itself; the callers here are the studio's synchronous HTTP
handlers, so everything public is a plain blocking function with a short
timeout.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

try:
    import aioesphomeapi
except ModuleNotFoundError:      # pragma: no cover — exercised by CI, not here
    # OPTIONAL. This leg only matters for the all-in-flash build, which serves
    # no HTTP; the SD build on the porch, the emulator and every test reach the
    # castle over port 80. A studio without the library still serves the desk
    # and relays everything — it just cannot talk to a flash-only castle, and
    # `connected()` says so rather than the import taking the server down
    # (CI installs numpy/scipy/pyyaml and nothing else, and hit exactly that).
    aioesphomeapi = None         # type: ignore[assignment]

# The client narrates every reconnect attempt at INFO/ERROR; in a studio
# that is one line per 5 s per dead castle, forever. Real trouble is WARNING+.
logging.getLogger("aioesphomeapi").setLevel(logging.WARNING)

PORT = 6053
#: One command round-trip; castle_link's own TIMEOUT_S guards the HTTP leg.
CALL_TIMEOUT_S = 3.0
_RETRY_S = 5.0

_lock = threading.Lock()
#: Keyed store rather than a bare module global, so refreshing it is a
#: mutation and not a rebind (ruff PLW0603) — same trick as castle_link.
_links: dict[str, "_Link"] = {}


class _Link:
    """The daemon thread: one connection, live state cache, reconnects."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.loop = asyncio.new_event_loop()
        self.api: aioesphomeapi.APIClient | None = None
        self.keys: dict[str, int] = {}
        self.states: dict[int, Any] = {}
        self.version = ""
        self.connected = False
        self.closing = False
        self._wake = asyncio.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="castle-native")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self._wake = asyncio.Event()
        while not self.closing:
            self._wake.clear()

            async def on_stop(expected: bool) -> None:
                self.connected = False
                self._wake.set()

            try:
                api = aioesphomeapi.APIClient(self.host, PORT, None)
                await api.connect(login=True, on_stop=on_stop)
                info = await api.device_info()
                self.version = info.project_version or info.esphome_version
                entities, _ = await api.list_entities_services()
                self.keys = {e.object_id: e.key for e in entities}
                api.subscribe_states(
                    lambda s: self.states.__setitem__(s.key, s))
                self.api = api
                self.connected = True
                await self._wake.wait()            # until dropped or closed
            except Exception:
                self.connected = False
            if self.closing:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), _RETRY_S)
            except TimeoutError:
                pass

    def close(self) -> None:
        """End the thread: no more reconnects. Tests only — the studio keeps
        its links for life."""
        self.closing = True
        self.connected = False
        self.loop.call_soon_threadsafe(self._wake.set)

    # -- called from the studio's threads ---------------------------------

    def _submit(self, fn: Any) -> bool:
        """Run one client call on the loop thread; False if it failed."""
        if not self.connected or self.api is None:
            return False

        async def call() -> None:
            fn(self.api)

        try:
            fut = asyncio.run_coroutine_threadsafe(call(), self.loop)
            fut.result(timeout=CALL_TIMEOUT_S)
            return True
        except Exception:
            return False

    def _text(self, object_id: str) -> str:
        s = self.states.get(self.keys.get(object_id, -1))
        return str(getattr(s, "state", "") or "")


def close_all() -> None:
    """Drop every link and join its thread (test teardown)."""
    with _lock:
        links = list(_links.values())
        _links.clear()
    for ln in links:
        ln.close()
        ln.thread.join(timeout=2)


def available() -> bool:
    """Is the native leg even possible here? False without aioesphomeapi."""
    return aioesphomeapi is not None


def _get(host: str) -> _Link:
    with _lock:
        if host not in _links:
            _links[host] = _Link(host)
        return _links[host]


def connected(host: str) -> bool:
    """Is the native leg actually talking to `host` right now?

    The bridge asks this BEFORE translating a verb: a castle that serves no
    HTTP and has no native session is simply down, and every verb must say
    so. (Pass 1 of the dogfood found Stop answering 200 "queued" to a dead
    castle, because the stub's key lookups all came back empty.)
    """
    return available() and _get(host).connected


def status(host: str) -> dict[str, Any] | None:
    """The desk's status shape, from native entity state; None if offline."""
    if not available():
        return None
    ln = _get(host)
    if not ln.connected:
        return None
    out: dict[str, Any] = {
        "version": ln.version,
        "scene": ln._text("current_scene"),
        "track": ln._text("current_track"),
        "native": True,
    }
    # Field parity with the SD build's HTTP status — the desk renders a
    # missing sd_mounted as "no SD", which on the SD build is a lie.
    sd = ln.states.get(ln.keys.get("sd_card_present", -1))
    if sd is not None:
        out["sd_mounted"] = bool(getattr(sd, "state", False))
    mp = ln.states.get(ln.keys.get("castle_audio", -1))
    vol = getattr(mp, "volume", None)
    if vol is not None:
        out["volume"] = round(float(vol) * 100)
    return out


def scene(host: str, scene_id: str) -> bool:
    """Press the firmware's scene__<id> button."""
    if not available():
        return False
    ln = _get(host)
    key = ln.keys.get(f"scene__{scene_id}")
    if key is None:
        return False
    return ln._submit(lambda api: api.button_command(key))


def stop(host: str) -> bool:
    """The desk's Stop: halt audio, then blackout the zones."""
    if not available():
        return False
    ln = _get(host)
    media, blackout = ln.keys.get("castle_audio"), ln.keys.get("blackout")
    # Nothing to press means nothing was stopped: False, never a vacuous
    # True — "press ■, see ✓, castle keeps blaring" is the worst night.
    if not ln.connected or (media is None and blackout is None):
        return False
    ok = True
    if media is not None:
        ok &= ln._submit(lambda api: api.media_player_command(
            media, command=aioesphomeapi.MediaPlayerCommand.STOP))
    if blackout is not None:
        ok &= ln._submit(lambda api: api.button_command(blackout))
    return ok


def volume(host: str, pct: int) -> bool:
    """0-100, same contract as the SD build's /api/volume."""
    if not available():
        return False
    ln = _get(host)
    key = ln.keys.get("castle_audio")
    if key is None or not 0 <= pct <= 100:
        return False
    return ln._submit(lambda api: api.media_player_command(
        key, volume=pct / 100.0))
