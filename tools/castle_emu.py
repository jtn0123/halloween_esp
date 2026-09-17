"""A castle on the desk: emulates the SD build's HTTP surface for testing.

The real device (firmware/sd_web.h) can only be exercised by plugging it in.
This serves the same routes with the same validation, the same error strings
and the same queued-action semantics, so the whole chain — desk → studio
relay → castle — runs end-to-end on the Mac with zero hardware:

    .venv/bin/python tools/castle_emu.py 8093 &
    CASTLE_HOST=127.0.0.1:8093 tools/studio_launch.sh

Three files: this one is the castle's STATE (the card directory, the
mirrored show state, the pending-action mailbox and its 200 ms tick);
castle_emu_http.py is the handlers; castle_emu_wire.py is the byte-level
port of sd_web.h's routing/decoding/validation that the contract test
holds to the C.

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
    ISSUE-007 — and clamps to MAX_VOLUME_PCT like castle_sd.yaml does.
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

import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import castle_emu_wire as wire
from castle_emu_clock import (
    BYTES_PER_S,
    SOUND_WAIT_S,
    SPEAKER_START_S,
    audio_state,
    silence_until,
)
from castle_emu_events import Events
from castle_emu_http import OTA_SLOT, Handler

#: The device applies queued actions on its main-loop interval.
APPLY_DELAY_S = 0.2
#: castle_sd.yaml clamps /api/volume to rig.h's kMaxVolumePct — scenes.yaml
#: hardware.audio.max_volume — and so does this. test_castle_emu holds them equal.
MAX_VOLUME_PCT = 100

#: Used only when no scenes.yaml can be found — the firmware seeds its list
#: from the generated show, so the emulator reads the same source of truth
#: (CASTLE_SCENES, else the repo's scenes/scenes.yaml) and 404s exactly the
#: ids the real castle would.
DEFAULT_SCENES = ["vigil", "storm", "arrival", "stop"]
ROOT = Path(__file__).resolve().parent.parent


def a_show_file(src: Path) -> Path | None:
    """`src`, if it is plausibly a show to read — else None.

    The path arrives from the environment (CASTLE_SCENES) or the command
    line, so it is checked BEFORE it is opened rather than after: resolved,
    so `..` cannot point somewhere else halfway through, and required to be
    a regular file with a YAML name. A directory, a device node or a socket
    is not a show, and the caller already knows what to do with None.
    """
    try:
        resolved = src.expanduser().resolve()
    except (OSError, RuntimeError):  # loops, unreadable parents
        return None
    if resolved.suffix.lower() not in (".yaml", ".yml"):
        return None
    return resolved if resolved.is_file() else None


def show_scene_ids(path: Path | None = None) -> list[str] | None:
    """Scene ids from a scenes.yaml — CASTLE_SCENES, else the repo's — or
    None when there is no readable show to seed from."""
    import yaml

    src = a_show_file(
        path or Path(os.environ.get("CASTLE_SCENES") or ROOT / "scenes" / "scenes.yaml")
    )
    if src is None:
        return None
    try:
        doc = yaml.safe_load(src.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    # "scenes:" with nothing under it parses to None — an empty sandbox
    # show (the e2e suite's) must seed the defaults, not crash the emulator.
    ids = [str(sc["id"]) for sc in (doc.get("scenes") or []) if "id" in sc]
    return [*ids, "stop"] if ids else None


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
        self.pir = {"armed": True, "cooldown_s": 60, "scene": "storm"}
        self.light = "show"
        self.boot = time.monotonic()


def _arm_clock(st: _State, audio: Path | None) -> None:
    """restart_audio_clock(): a PLAY or a SCENE arms the clock, on state the
    caller locks.

    Armed means playing:true and position_ms:0 from the command itself,
    before any audio exists (firmware/sd_web_state.h:restart_audio_clock),
    and for kSoundWaitUs after it if the speaker is never heard from —
    then one `silent` line and idle. So the arming does NOT depend on the
    card (C3), and a file the card does not have simply never sounds (C4):
    it gets no duration to invent a `sound` event and a moving position_ms
    out of.
    """
    st.track_started = time.monotonic()
    # A file on the card plays for as long as its bytes last (96 kbps); one
    # that is not there has no duration at all, so it never sounds.
    dur = max(1, audio.stat().st_size // BYTES_PER_S) if audio is not None else 0
    st.track_ends = st.track_started + dur
    st.starting_until = st.track_started + SOUND_WAIT_S


def _scene_stop(st: _State) -> None:
    """`scene_stop` and the one line beside it, on state the caller locks.

    The firmware publishes scene="stop" (not ""), silences the media player,
    and since v5.58 hands the strips back — a page light show drove them
    through lights_override and they held its colour through a stop.
    """
    st.starting_until = silence_until(
        st.track_started, st.track_ends, st.starting_until, time.monotonic()
    )
    st.scene, st.track, st.light = "stop", "", "off"


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
        self.scenes = (
            scenes if scenes is not None else show_scene_ids() or list(DEFAULT_SCENES)
        )
        self.version = version
        #: h_status's "missing": the boot manifest's comma-separated list of
        #: scene files the card lacks. Tests set it to rehearse the escaping.
        self.missing = ""
        self.wedge = wedge
        self.sd_mounted = sd_mounted
        #: h_ota's ceiling: the app partition of the build being rehearsed.
        self.ota_slot = ota_slot
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
        threading.Thread(
            target=self._ticker, daemon=True, name="castle-emu-tick"
        ).start()

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

    def _ticker(self) -> None:
        while True:
            time.sleep(APPLY_DELAY_S)
            with self.state.lock:
                # take_pending(): the restart latch drains first and leaves
                # the slot alone, so a command queued beside it still lands.
                taken: tuple[str, str] | None
                if self._restart_pending:
                    self._restart_pending = False
                    taken = ("RESTART", "")
                else:
                    taken, self._pending = self._pending, None
            self._mirror()
            if taken is not None:
                self.events.record_action(*taken, self.uptime_ms())
                try:
                    self._apply(*taken)
                finally:
                    self.applied.append(taken)

    def uptime_ms(self) -> int:
        """esp_timer's clock as the ring stamps it: milliseconds since boot."""
        return int((time.monotonic() - self.state.boot) * 1000)

    def _mirror(self) -> None:
        """The interval's mirroring half: the dropped-frame line (at most one
        a second) and the audio clock's start/end transitions."""
        st = self.state
        now = time.monotonic()
        with st.lock:
            playing, _pos = audio_state(
                st.track, st.track_started, st.track_ends, st.starting_until, now
            )
            sounding = bool(st.track) and (
                st.track_started + SPEAKER_START_S <= now < st.track_ends
            )
        t_ms = self.uptime_ms()
        self.events.note_light_evictions(t_ms)
        # The song ended. Only a RAW file loses its name here:
        # castle_sd_common.yaml clears current_track on the tick mirror_audio
        # reports the END and only when current_scene is "stop", so an
        # authored scene keeps naming its track until scene_stop — and a
        # track whose sound never came keeps its name for the whole grace.
        if self.events.note_audio(sounding, playing, t_ms):
            with st.lock:
                if st.scene == "stop":
                    st.track = ""

    def _apply(self, action: str, arg: str) -> None:
        st = self.state
        with st.lock:
            if action == "VOLUME":
                st.volume = min(int(arg), MAX_VOLUME_PCT)
            elif action == "PLAY":
                f = self.sd_dir / arg
                st.track = arg
                # A raw file has no scene (v5.52: the firmware publishes
                # "stop" so a live light frame does not stop the file).
                st.scene = "stop"
                _arm_clock(st, f if f.is_file() else None)
            elif action == "SCENE":
                st.scene = arg
                # run_scene hands the strips back to Show (gen_esphome.py);
                # only "halt" leaves whatever a test pattern set.
                if arg != "halt":
                    st.light = "show"
                audio = self.sd_dir / "scenes" / f"{arg}.mp3"
                if audio.is_file():
                    st.track = audio.name
                # C3: the clock is armed for a scene whether or not its
                # track is on the card. restart_audio_clock() does not look
                # at the card at all — it publishes playing:true from the
                # command, and a scene whose audio failed to sync reads
                # "starting" on the device for the whole grace and then
                # ends once. Deriving `playing` from the file made the
                # emulator answer idle for the same request.
                _arm_clock(st, audio if audio.is_file() else None)
            elif action == "STOP":
                # Firmware STOP is `scene_stop` only: the evening playlist
                # keeps running and starts the next scene after the gap.
                # Ending the night is SHOW "0" (/api/show/stop), below.
                _scene_stop(st)
            elif action == "BLACKOUT":
                # #25, the panic switch: playlist, scene and audio all off.
                _scene_stop(st)
                st.show_on = False
            elif action == "SHOW":
                st.show_on = arg == "1"
                if not st.show_on:
                    # Quiet means the playlist AND the scene it was mid-way
                    # through — castle_sd_common.yaml runs scene_stop too.
                    _scene_stop(st)
            elif action == "LIGHT":
                st.light = arg
            elif action == "PIRCFG":
                armed, cool, scene = [*arg.split("|"), "", "", ""][:3]
                if armed:
                    st.pir["armed"] = armed == "1"
                if cool:
                    st.pir["cooldown_s"] = int(cool)
                if scene:
                    st.pir["scene"] = scene
            elif action == "RESTART":
                st.boot = time.monotonic()
                st.scene, st.track, st.show_on = "", "", False
                st.starting_until = 0.0

    def status_json(self) -> dict[str, object]:
        st = self.state
        # Real numbers from the disk under the card dir — the point is that
        # the field EXISTS and is honest, same as v5.23's esp_vfs_fat_info.
        du = shutil.disk_usage(self.sd_dir)
        now = time.monotonic()
        with st.lock:
            playing, position_ms = audio_state(
                st.track, st.track_started, st.track_ends, st.starting_until, now
            )
            return {
                "version": self.version,
                "compiled": "emulated",
                "uptime_s": int(time.monotonic() - st.boot),
                "sd_mounted": self.sd_mounted,
                "psram_free_kb": 1800,
                "heap_free_kb": 96,
                "sd_total_kb": du.total // 1024 if self.sd_mounted else 0,
                "sd_free_kb": du.free // 1024 if self.sd_mounted else 0,
                "missing": self.missing,
                "volume": st.volume,
                "scene": st.scene,
                "track": st.track,
                # B1: the ids this "build" runs with — the same list
                # /api/scene checks, so the desk can spot a stale board.
                "scenes": ",".join(self.scenes),
                "show_on": st.show_on,
                # v5.52/5.55: the speaker's word, not the mailbox's, and a
                # clock that counts from the sound (castle_emu_clock).
                "playing": playing,
                "position_ms": position_ms,
                # v5.59: LIGHT frames the main loop ran, and the ones the
                # one-slot mailbox dropped before it could (sd_web_state.h).
                "light_applied": self.events.light_applied,
                "light_evicted": self.events.light_evicted,
                "pir": {
                    "armed": st.pir["armed"],
                    "cooldown_s": st.pir["cooldown_s"],
                    "scene": st.pir["scene"],
                },
            }

    def status_text(self) -> str:
        """h_status's template: numbers through the same formats, strings
        through json_escape — the C mirrors Python's json.dumps table, so a
        '"' in the track or the missing list goes out escaped on both."""
        s = self.status_json()
        pir = s["pir"]
        assert isinstance(pir, dict)
        b = {True: "true", False: "false"}

        def i(k: str) -> int:
            return int(str(s[k]))

        def t(k: str) -> str:
            return wire.json_escape(str(s[k]))

        return (
            '{"version":"%s","compiled":"%s","uptime_s":%d,'
            '"sd_mounted":%s,"psram_free_kb":%d,"heap_free_kb":%d,'
            '"sd_total_kb":%d,"sd_free_kb":%d,"missing":"%s",'
            '"volume":%d,"scene":"%s","track":"%s","scenes":"%s",'
            '"show_on":%s,"playing":%s,"position_ms":%d,'
            '"light_applied":%d,"light_evicted":%d,'
            '"pir":{"armed":%s,"cooldown_s":%d,"scene":"%s"}}'
            % (
                t("version"),
                t("compiled"),
                i("uptime_s"),
                b[bool(s["sd_mounted"])],
                i("psram_free_kb"),
                i("heap_free_kb"),
                i("sd_total_kb"),
                i("sd_free_kb"),
                t("missing"),
                i("volume"),
                t("scene"),
                t("track"),
                t("scenes"),
                b[bool(s["show_on"])],
                b[bool(s["playing"])],
                i("position_ms"),
                i("light_applied"),
                i("light_evicted"),
                b[bool(pir["armed"])],
                int(pir["cooldown_s"]),
                wire.json_escape(str(pir["scene"])),
            )
        )


if __name__ == "__main__":  # `python tools/castle_emu.py 8093` — the CLI
    from castle_emu_cli import main

    main()
