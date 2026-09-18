"""A castle on the desk: emulates the SD build's HTTP surface for testing.

The real device (firmware/sd_web.h) can only be exercised by plugging it in.
This serves the same routes with the same validation, the same error strings
and the same queued-action semantics, so the whole chain — desk → studio
relay → castle — runs end-to-end on the Mac with zero hardware:

    .venv/bin/python tools/castle_emu.py 8093 &
    CASTLE_HOST=127.0.0.1:8093 tools/studio_launch.sh

Five files: this one is the castle's STATE (the card directory, the
mirrored show state and the pending-action mailbox); castle_emu_loop.py is
the main loop that acts on it (the 200 ms tick, the mirror and the one
action-per-tick switch); castle_emu_status.py is the one reply built from it
(`/api/status`, as data and as h_status's own bytes); castle_emu_http.py is
the handlers; castle_emu_wire.py is the byte-level port of sd_web.h's
routing/decoding/validation that the contract test holds to the C.

Fidelity notes, each mirrored from sd_web.h on purpose:
  - POSTs answer {"queued":true} immediately; the state changes ~200 ms
    later (the device's pending-action mailbox + main-loop interval). A UI
    that reads state right after a click sees the OLD state, exactly as it
    would on the porch.
  - The mailbox is ONE slot: two commands inside the same 200 ms tick and
    only the later one runs (set_pending overwrites). A colour-picker drag
    lands its last colour; a stop-then-scene inside a tick loses the stop.
    Two exceptions, both the firmware's: a LIGHT never evicts a command of
    another kind, and RESTART waits in a latch of its own.
  - /api/volume takes digits only, 0..100 — atoi("abc")-is-0 was dogfood
    ISSUE-007 — and clamps to MAX_VOLUME_PCT like the firmware does.
  - /api/scene 404s an unknown id — {"queued":true} for a typo was 008.
  - Filenames are one path component, nothing hidden, same safe_name rule,
    measured in BYTES as the board measures them.
  - --wedge replays the pre-v5.22 firmware defect: while a track plays,
    every request stalls. Lets the desk's "castle not answering" path be
    rehearsed without flashing the old build.

The card is a directory (--dir, default a temp dir seeded with two tones);
uploads and deletes are real files, so a send can be verified with ls.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import castle_emu_loop as loop
import castle_emu_wire as wire
from castle_emu_events import Events
from castle_emu_http import OTA_SLOT, Handler
from castle_emu_loop import APPLY_DELAY_S, MAX_VOLUME_PCT
from castle_emu_scenes import card_scene_ids, show_scene_ids
from castle_emu_status import status_json, status_text

#: The 200 ms tick and the volume ceiling live with the loop that uses them
#: (castle_emu_loop.py) and are re-exported here: tests read them off the
#: castle, and where the switch statement sits is not their business.
__all__ = [
    "APPLY_DELAY_S",
    "DEFAULT_SCENES",
    "MAX_VOLUME_PCT",
    "MISSING_MAX",
    "CastleEmu",
    "safe_name",
]

#: Used only when no scenes.yaml can be found — the firmware seeds its list
#: from the generated show, so the emulator reads the same source of truth
#: (CASTLE_SCENES, else the repo's scenes/scenes.yaml) and 404s exactly the
#: ids the real castle would.
DEFAULT_SCENES = ["vigil", "storm", "arrival", "stop"]

#: castle_web::kMissingMax — how many names /api/status's `missing` will carry
#: before it stops growing. It is in a polled reply, so it is bounded.
MISSING_MAX = 16


def safe_name(n: str) -> bool:
    """One path component, nothing hidden — sd_web.h's rule, on the UTF-8
    bytes the board would see."""
    return wire.safe_name(n.encode("utf-8", "surrogateescape"))


class _State:
    """What the firmware's globals hold, behind one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.volume = 70
        self.scene = ""
        self.track = ""
        self.track_ends = 0.0
        #: v5.52's audio clock: when the current track began, for position_ms.
        self.track_started = 0.0
        #: Armed-clock grace: "starting" until here, with nothing playing.
        self.starting_until = 0.0
        self.show_on = False
        # armed=False since v5.69: the AM312 is not wired on the S3 carrier,
        # so the firmware's switch is RESTORE_MODE ALWAYS_OFF and the castle
        # boots disarmed. /api/pir?armed=1 still arms it for the session.
        self.pir = {"armed": False, "cooldown_s": 60, "scene": "storm"}
        self.light = "show"
        self.cues = 0  # v5.63: the card show loaded for the raw track
        #: J2: when the running scene's authored length runs out, and whether
        #: it loops. castle_scenes::finished() and loops(), as two numbers —
        #: 0.0 means no scene is holding the stage.
        self.scene_ends = 0.0
        self.scene_loops = False
        self.boot = time.monotonic()


class CastleEmu(ThreadingHTTPServer):
    """The emulated castle. Construct with port 0 to get an ephemeral port."""

    daemon_threads = True
    # The desk polls, the studio relays and a fuzz storms; a 5-deep backlog
    # (the Python default) turns bursts into refused connects.
    request_queue_size = 64

    def handle_error(self, request: object, client_address: object) -> None:
        """A client that hung up mid-reply is not an error worth a traceback.

        socketserver prints every exception a handler thread lets through.
        Handler._dispatch already swallows EPIPE/ECONNRESET from a handler,
        but the reply's status line and headers are written by http.server
        itself, outside that try - so the same hang-up can surface here.
        The real httpd's send just fails and the handler returns; so does
        this. Everything else still prints, as it should.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]

    def __init__(
        self,
        port: int = 0,
        sd_dir: Path | None = None,
        scenes: list[str] | None = None,
        version: str = "5.40",
        wedge: bool = False,
        sd_mounted: bool = True,
        serial: bool = False,
        ota_slot: int = OTA_SLOT,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.state = _State()
        self.sd_dir = sd_dir or Path(tempfile.mkdtemp(prefix="castle-emu-sd-"))
        self.sd_dir.mkdir(parents=True, exist_ok=True)
        # The card first (v5.67: /sd/scenes/show.man IS the scene list), then
        # the show this emulator was pointed at, standing in for the ids a
        # real image was compiled with, then the defaults. Read once here, the
        # way the firmware seeds its list once at boot — a manifest published
        # while the castle is up is not visible until it reboots, on both
        # castles, and a test that wants the new list restarts the emulator.
        self.scenes = (
            scenes
            if scenes is not None
            else card_scene_ids(self.sd_dir) or show_scene_ids() or list(DEFAULT_SCENES)
        )
        self.version = version
        #: h_status's "missing": the boot manifest's comma-separated list of
        #: scene files the card lacks. Tests set it to rehearse the escaping.
        self.missing = ""
        self.wedge = wedge
        self.sd_mounted = sd_mounted
        #: h_ota's ceiling: the app partition of the build being rehearsed.
        self.ota_slot = ota_slot
        #: castle_sd::g_quiesce — up while flash is being written (h_ota).
        #: J3 (grade report 2026-09-17 pm): a scene start is refused while it
        #: is, on both castles. Settable from a test so the gate can be driven
        #: without an OTA in flight, exactly as CASTLE_QUIESCE does for the C.
        self.quiesce = False
        #: write_body's free-space precondition (B3): KB free the emulated
        #: card claims. None = report the disk's real number and never 507.
        self.sd_free_kb: int | None = None
        # The real httpd is ONE task: a long PUT holds every other request
        # (the status poll included) until it finishes. --serial rehearses
        # that; the default threads so the bench stays snappy.
        self.serial = threading.Lock() if serial else None
        #: set_pending's single slot: (action, arg) or None.
        self._pending: tuple[str, str] | None = None
        #: RESTART's own latch, drained ahead of the slot (sd_web_state.h).
        self._restart_pending = False
        self.applied: list[tuple[str, str]] = []  # what the tick ran, for tests
        #: The event ring and the light counters (castle_emu_events.py):
        #: what the main loop DID, which a 1 Hz status poll cannot see.
        self.events = Events()
        loop.start_ticker(self)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> None:
        threading.Thread(
            target=self.serve_forever, daemon=True, name="castle-emu"
        ).start()

    # -- the pending-action mailbox ---------------------------------------

    def queue(self, action: str, arg: str) -> None:
        """set_pending(): the newest command replaces whatever waited, with
        the two exceptions firmware/sd_web_state.h makes. RESTART has a latch
        of its own, so a flashed image always reboots; and a LIGHT (streamed
        at ~4 Hz by a synced import) never evicts a command of another kind —
        it is dropped instead, and counted as the eviction it is."""
        with self.state.lock:
            if action == "RESTART":
                self._restart_pending = True
                return
            # Either way a frame is lost: LIGHT over LIGHT drops the one
            # underneath, LIGHT behind another kind drops ITSELF. Both count
            # (v5.60, A6) — until then only the first did, and a page could
            # never make applied + evicted add up to what it had sent.
            if action == "LIGHT" and self._pending is not None:
                self.events.light_evicted += 1
                if self._pending[0] != "LIGHT":
                    return
            self._pending = (action, arg)

    def uptime_ms(self) -> int:
        """esp_timer's clock as the ring stamps it: milliseconds since boot."""
        return int((time.monotonic() - self.state.boot) * 1000)

    def heal_missing(self, name: str) -> None:
        """castle_web::heal_missing: REMOVE one name from /api/status's
        `missing`. A cue file that was missing and has since been published is
        not missing any more, and until v5.68 the only way to say so was a
        reboot — so the operator who fixed the card still read
        `missing:"storm.cue"` from the castle that was by then playing the
        full show. A successful start is the one moment either castle has
        first-hand evidence about a name."""
        have = [n for n in self.missing.split(",") if n and n != name]
        self.missing = ",".join(have)

    def note_missing(self, name: str) -> None:
        """castle_web::note_missing: ADD one name to /api/status's `missing`,
        if it is not already listed, bounded at kMissingMax. The boot check
        speaks for the card as it was at mount; a scene that could not be
        started an hour later is a second fact about the same card."""
        have = self.missing.split(",") if self.missing else []
        if name in have or len(have) >= MISSING_MAX:
            return
        self.missing = ",".join([*have, name])

    def _apply(self, action: str, arg: str) -> None:
        """castle_emu_loop.apply — one queued action, as the tick runs it.
        Kept as a method because tests drive a single action through it."""
        loop.apply(self, action, arg)

    def status_json(self) -> dict[str, object]:
        """castle_emu_status.status_json — the reply as data."""
        return status_json(self)

    def status_text(self) -> str:
        """castle_emu_status.status_text — the reply as h_status's bytes."""
        return status_text(self)


if __name__ == "__main__":  # `python tools/castle_emu.py 8093` — the CLI
    from castle_emu_cli import main

    main()
