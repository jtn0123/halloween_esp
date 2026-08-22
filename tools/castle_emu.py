"""A castle on the desk: emulates the SD build's HTTP surface for testing.

The real device (firmware/sd_web.h) can only be exercised by plugging it in.
This serves the same routes with the same validation, the same error strings
and the same queued-action semantics, so the whole chain — desk → studio
relay → castle — runs end-to-end on the Mac with zero hardware:

    .venv/bin/python tools/castle_emu.py 8093 &
    CASTLE_HOST=127.0.0.1:8093 .venv/bin/python tools/studio.py

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
  - /api/volume takes digits only, 0..100 — atoi("abc")-is-0 was dogfood
    ISSUE-007.
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

import argparse
import os
import shutil
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import castle_emu_wire as wire
from castle_emu_http import Handler

#: The device applies queued actions on its main-loop interval.
APPLY_DELAY_S = 0.2
#: Rough playback clock: 96 kbps MP3 is ~12 kB of file per second.
BYTES_PER_S = 12000

#: Used only when no scenes.yaml can be found — the firmware seeds its list
#: from the generated show, so the emulator reads the same source of truth
#: (CASTLE_SCENES, else the repo's scenes/scenes.yaml) and 404s exactly the
#: ids the real castle would.
DEFAULT_SCENES = ["vigil", "storm", "arrival", "stop"]
ROOT = Path(__file__).resolve().parent.parent


def show_scene_ids(path: Path | None = None) -> list[str] | None:
    """Scene ids from a scenes.yaml — CASTLE_SCENES, else the repo's — or
    None when there is no readable show to seed from."""
    import yaml
    src = path or Path(os.environ.get("CASTLE_SCENES")
                       or ROOT / "scenes" / "scenes.yaml")
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
        self.show_on = False
        self.pir = {"armed": True, "cooldown_s": 60, "scene": "storm"}
        self.light = "show"
        self.boot = time.monotonic()


class CastleEmu(ThreadingHTTPServer):
    """The emulated castle. Construct with port 0 to get an ephemeral port."""

    daemon_threads = True
    # The desk polls, the studio relays and a fuzz storms; a 5-deep backlog
    # (the Python default) turns bursts into refused connects.
    request_queue_size = 64

    def __init__(self, port: int = 0, sd_dir: Path | None = None,
                 scenes: list[str] | None = None, version: str = "5.31",
                 wedge: bool = False, sd_mounted: bool = True,
                 serial: bool = False) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.state = _State()
        self.sd_dir = sd_dir or Path(tempfile.mkdtemp(prefix="castle-emu-sd-"))
        self.sd_dir.mkdir(parents=True, exist_ok=True)
        self.scenes = (scenes if scenes is not None
                       else show_scene_ids() or list(DEFAULT_SCENES))
        self.version = version
        #: h_status's "missing": the boot manifest's comma-separated list of
        #: scene files the card lacks. Tests set it to rehearse the escaping.
        self.missing = ""
        self.wedge = wedge
        self.sd_mounted = sd_mounted
        # The real httpd is ONE task: a long PUT holds every other request
        # (the status poll included) until it finishes. --serial rehearses
        # that; the default threads so the bench stays snappy.
        self.serial = threading.Lock() if serial else None
        #: set_pending's single slot: (action, arg) or None.
        self._pending: tuple[str, str] | None = None
        self.applied: list[tuple[str, str]] = []   # what the tick ran, for tests
        threading.Thread(target=self._ticker, daemon=True,
                         name="castle-emu-tick").start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True,
                         name="castle-emu").start()

    # -- the pending-action mailbox ---------------------------------------

    def queue(self, action: str, arg: str) -> None:
        """set_pending(): the newest command replaces whatever waited."""
        with self.state.lock:
            self._pending = (action, arg)

    def _ticker(self) -> None:
        while True:
            time.sleep(APPLY_DELAY_S)
            with self.state.lock:
                taken, self._pending = self._pending, None
                st = self.state
                if st.track and time.monotonic() > st.track_ends:
                    st.track = ""          # the song ended on its own
            if taken is not None:
                try:
                    self._apply(*taken)
                finally:
                    self.applied.append(taken)

    def _apply(self, action: str, arg: str) -> None:
        st = self.state
        with st.lock:
            if action == "VOLUME":
                st.volume = int(arg)
            elif action == "PLAY":
                f = self.sd_dir / arg
                size = f.stat().st_size if f.is_file() else 0
                st.track = arg
                st.track_ends = time.monotonic() + max(1, size // BYTES_PER_S)
            elif action == "SCENE":
                st.scene = arg
                audio = self.sd_dir / "scenes" / f"{arg}.mp3"
                if audio.is_file():
                    st.track = audio.name
                    st.track_ends = (time.monotonic()
                                     + max(1, audio.stat().st_size // BYTES_PER_S))
            elif action in ("STOP", "BLACKOUT"):
                st.scene, st.track, st.show_on = "", "", False
            elif action == "SHOW":
                st.show_on = arg == "1"
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

    def status_json(self) -> dict[str, object]:
        st = self.state
        # Real numbers from the disk under the card dir — the point is that
        # the field EXISTS and is honest, same as v5.23's esp_vfs_fat_info.
        du = shutil.disk_usage(self.sd_dir)
        with st.lock:
            return {
                "version": self.version, "compiled": "emulated",
                "uptime_s": int(time.monotonic() - st.boot),
                "sd_mounted": self.sd_mounted,
                "psram_free_kb": 1800, "heap_free_kb": 96,
                "sd_total_kb": du.total // 1024 if self.sd_mounted else 0,
                "sd_free_kb": du.free // 1024 if self.sd_mounted else 0,
                "missing": self.missing,
                "volume": st.volume, "scene": st.scene, "track": st.track,
                "show_on": st.show_on,
                "pir": {"armed": st.pir["armed"],
                        "cooldown_s": st.pir["cooldown_s"],
                        "scene": st.pir["scene"]},
            }

    def status_text(self) -> str:
        """h_status's template: numbers through the same formats, strings
        through json_escape — the C mirrors Python's json.dumps table, so a
        '"' in the track or the missing list goes out escaped on both."""
        s = self.status_json()
        pir = s["pir"]
        assert isinstance(pir, dict)
        b = {True: "true", False: "false"}
        i, t = (lambda k: int(str(s[k]))), (lambda k: wire.json_escape(str(s[k])))
        return ('{"version":"%s","compiled":"%s","uptime_s":%d,'
                '"sd_mounted":%s,"psram_free_kb":%d,"heap_free_kb":%d,'
                '"sd_total_kb":%d,"sd_free_kb":%d,"missing":"%s",'
                '"volume":%d,"scene":"%s","track":"%s","show_on":%s,'
                '"pir":{"armed":%s,"cooldown_s":%d,"scene":"%s"}}'
                % (t("version"), t("compiled"), i("uptime_s"),
                   b[bool(s["sd_mounted"])], i("psram_free_kb"), i("heap_free_kb"),
                   i("sd_total_kb"), i("sd_free_kb"), t("missing"),
                   i("volume"), t("scene"), t("track"), b[bool(s["show_on"])],
                   b[bool(pir["armed"])], int(pir["cooldown_s"]),
                   wire.json_escape(str(pir["scene"]))))



def _seed(card: Path) -> None:
    """Two placeholder 'songs' so the desk has something to list and play."""
    for name, kb in (("wicked_winds.mp3", 280), ("ghostbusters.mp3", 960)):
        f = card / name
        if not f.exists():
            f.write_bytes(b"\xff\xfb" + b"\x00" * (kb * 1024 - 2))
    (card / "logs").mkdir(exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", type=int, default=8093)
    ap.add_argument("--dir", type=Path, default=None,
                    help="directory that plays the SD card (default: temp, seeded)")
    ap.add_argument("--wedge", action="store_true",
                    help="replay the pre-v5.22 wedge: stall requests while playing")
    ap.add_argument("--no-sd", action="store_true",
                    help="pretend the card is missing")
    ap.add_argument("--serial", action="store_true",
                    help="one request at a time, like the device's single httpd task")
    ap.add_argument("--scenes", default=None,
                    help="scene ids: a comma list, or a scenes.yaml "
                         "(default: $CASTLE_SCENES, else scenes/scenes.yaml)")
    args = ap.parse_args()
    scenes: list[str] | None = None
    if args.scenes:
        scenes = (show_scene_ids(Path(args.scenes)) if args.scenes.endswith(".yaml")
                  else [x.strip() for x in args.scenes.split(",") if x.strip()])
    emu = CastleEmu(port=args.port, sd_dir=args.dir, wedge=args.wedge,
                    sd_mounted=not args.no_sd, serial=args.serial,
                    scenes=scenes)
    if args.dir is None:
        _seed(emu.sd_dir)
    print(f"castle emulator on http://127.0.0.1:{emu.port}  card={emu.sd_dir}"
          + ("  [WEDGE MODE]" if args.wedge else "")
          + ("  [SERIAL]" if args.serial else ""))
    print(f"  scenes: {', '.join(emu.scenes)}")
    print(f"  point the studio at it:  CASTLE_HOST=127.0.0.1:{emu.port}")
    emu.serve_forever()


if __name__ == "__main__":
    main()
