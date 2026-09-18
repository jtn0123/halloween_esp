"""The scene manifest and the runner, held together across two languages.

tools/scene_manifest.py writes /sd/scenes/show.man, firmware/castle_scenes.h
reads it, and nothing but this test makes the two agree — the same arrangement
tests/test_cue_file_cxx.py has for the cue file. tests/cxx/scenes_check.cpp
compiles the REAL header against the fake ESP-IDF in tests/cxx/shim/ and runs
it over a card this test writes.

What is covered, and why each one is here rather than trusted:

  * the manifest's four refusals — a truncated file, a wrong magic, a version
    this build does not read, more scenes than kMaxScenes. A manifest is read
    at boot to seed /api/scene's known-id list, so a file this reader half
    believes is a castle that accepts scene names it cannot play;
  * the runner's state machine: start, the length it waits for, looping,
    stop, a restart mid-scene, and scene -> raw song -> scene, which is the
    sequence that used to leave a looping scene's re-run fighting the file
    the operator chose;
  * J2 (grade report 2026-09-17 pm), the END of a non-looping scene: the cue
    blob has to go back to PSRAM there, and until v5.69 it did not;
  * a missing cue file, which must be a scene that still plays its audio
    under its base look rather than a refusal;
  * J7 (grade report 2026-09-17), the chunked read: a cue file larger than
    kReadChunk must load to the same bytes as a small one;
  * the level clamp, also J7: a u8 level of 254 in a hand-edited or
    half-written file must not reach the strips as 2.54.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import scene_manifest

SRC = ROOT / "tests" / "cxx" / "scenes_check.cpp"
COMPILER = shutil.which("clang++") or shutil.which("g++")
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "tests" / "cxx" / "shim"), "-I", str(ROOT / "firmware")]  # fmt: skip
IN_CI = bool(os.environ.get("CI"))
ZONES = ["towerL", "towerR", "door"]

#: Two scenes, one of which loops — the two kinds the runner branches on.
SCENES: list[dict[str, Any]] = [
    {
        "id": "vigil",
        "duration_ms": 30000,
        "loop": True,
        "volume": 0.45,
        "base": {"towerL": "candle", "towerR": "candle", "door": "ember"},
        "levels": {"towerL": 0.4, "towerR": 0.4, "door": 0.5},
        "cues": [{"t": 1200, "op": "strike", "zone": "door", "intensity": 0.5}],
    },
    {
        "id": "storm",
        "duration_ms": 8000,
        "volume": 0.9,
        "base": {"towerL": "chill", "towerR": "chill", "door": "off"},
        "cues": [{"t": 40, "op": "set", "zone": "towerL", "effect": "seance"}],
    },
]


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class SceneRunnerCase(unittest.TestCase):
    exe: Path
    _build: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        cls._build = tempfile.TemporaryDirectory()
        cls.exe = Path(cls._build.name) / "scenes_check"
        built = subprocess.run([COMPILER, *FLAGS, str(SRC), "-o", str(cls.exe)],
                               capture_output=True, text=True, check=False)  # fmt: skip
        assert built.returncode == 0, built.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build.cleanup()

    def setUp(self) -> None:
        self.card = Path(self.enterContext(tempfile.TemporaryDirectory()))

    # ── the card, written the way `sd_sync scenes` leaves it ───────────────

    def write_card(
        self,
        scenes: list[dict[str, Any]] | None = None,
        manifest: bytes | None = None,
        cues: bool = True,
        audio: bool = True,
    ) -> None:
        """One publish: show.man, a .cue per scene and a stand-in for each
        mp3. `manifest` overrides the bytes, for the refusal cases.

        The card is emptied first, so a second call inside one test is a
        RE-publish rather than a merge with what the first call left there.
        """
        scenes = SCENES if scenes is None else scenes
        for stale in self.card.iterdir():
            stale.unlink()
        (self.card / "show.man").write_bytes(
            scene_manifest.encode(scenes) if manifest is None else manifest
        )
        for i, s in enumerate(scenes, start=1):
            if cues:
                (self.card / f"{s['id']}.cue").write_bytes(
                    cue_file.encode(s, s.get("cues") or [], ZONES)
                )
            if audio:
                token = scene_manifest.audio_token(i, str(s["id"]))
                (self.card / f"{token}.mp3").write_bytes(b"\xff\xfb")

    def run_ops(self, *ops: str) -> list[str]:
        run = subprocess.run([str(self.exe), str(self.card), *ops],
                             capture_output=True, text=True, check=False)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run.stdout.splitlines()

    def one(self, *ops: str) -> str:
        """The LAST line, for an op sequence that ends in the assertion."""
        return self.run_ops(*ops)[-1]


class TestTheManifestIsRead(SceneRunnerCase):
    def test_the_ids_are_what_api_status_will_list(self) -> None:
        self.write_card()
        self.assertEqual(self.run_ops("ids"), ["ids vigil,storm"])
        # And Python reads back its own bytes the same way, so the CSV the
        # emulator serves cannot drift from the device's.
        self.assertEqual(
            scene_manifest.scene_ids(self.card / "show.man"), ["vigil", "storm"]
        )

    def test_no_manifest_at_all_is_an_empty_list_not_a_guess(self) -> None:
        """The boot seeding falls back to the compiled-in ids on "" — an
        empty string has to mean "this card told me nothing", because the
        alternative is /api/scene accepting names nothing can play."""
        self.assertEqual(self.run_ops("ids"), ["ids "])
        self.assertEqual(self.run_ops("missing"), ["missing show.man"])

    def test_a_manifest_this_build_cannot_read_is_refused_whole(self) -> None:
        good = scene_manifest.encode(SCENES)
        cases = {
            "empty": b"",
            "header only": good[: scene_manifest.HEADER.size],
            "truncated entry": good[:-1],
            "one byte too long": good + b"\x00",
            "wrong magic": b"XSMF" + good[4:],
            "future version": good[:4] + b"\x02" + good[5:],
            # entry_size is in the header exactly so that a future field is a
            # refusal and not a misread: every number after it would shift.
            "other entry size": (good[:8] + struct.pack("<I", 104) + good[12:]),
            # count is a u8 and kMaxScenes is 12; a card claiming more is the
            # one case where believing the file means reading past its end.
            "too many scenes": good[:5] + bytes([13]) + good[6:],
        }
        for why, blob in cases.items():
            with self.subTest(why=why):
                self.write_card(manifest=blob)
                self.assertEqual(self.run_ops("ids"), ["ids "])
                self.assertEqual(self.run_ops("missing"), ["missing show.man"])
                self.assertEqual(self.run_ops("begin:vigil")[0], "begin known=0")

    def test_python_refuses_the_same_files(self) -> None:
        """Both sides or neither: a blob the C reads and Python does not is a
        manifest the generator could write and no test would notice."""
        good = scene_manifest.encode(SCENES)
        for blob in (b"", good[:-1], good + b"\x00", b"XSMF" + good[4:],
                     good[:4] + b"\x02" + good[5:]):  # fmt: skip
            with self.assertRaises(ValueError):
                scene_manifest.decode(blob)

    def test_a_scene_the_manifest_does_not_list_is_not_started(self) -> None:
        self.write_card()
        first, state = self.run_ops("begin:seance")[:2]
        self.assertEqual(first, "begin known=0")
        self.assertIn("armed=0", state)
        self.assertIn("cues=0", state)
        # Not a prefix match either: "vigi" is not "vigil".
        self.assertEqual(self.run_ops("begin:vigi")[0], "begin known=0")

    def test_missing_names_the_audio_and_the_cue_file_differently(self) -> None:
        """They are different faults. No mp3 is a silent scene; no .cue is a
        scene that plays under a static base look. An operator reading
        /api/status needs to know which publish went wrong."""
        self.write_card(cues=False)
        self.assertEqual(self.run_ops("missing"), ["missing vigil.cue,storm.cue"])
        self.write_card(audio=False)
        self.assertEqual(self.run_ops("missing"), ["missing 01_vigil.mp3,02_storm.mp3"])
        self.write_card()
        self.assertEqual(self.run_ops("missing"), ["missing "])


class TestTheRunnerStateMachine(SceneRunnerCase):
    def test_a_start_takes_every_number_from_the_card(self) -> None:
        """What a generated script carried as literals: the audio token, the
        level, the length and the loop flag."""
        self.write_card()
        self.assertEqual(
            self.run_ops("start:vigil"),
            [
                "begin known=1",
                "audio 01_vigil",
                "cues loaded=1 count=1",
                (
                    "state running=vigil audio=01_vigil armed=1 loops=1 "
                    "len=30000 vol=0.45 cues=1"
                ),
            ],
        )
        self.assertEqual(
            self.run_ops("start:storm")[-1],
            "state running=storm audio=02_storm armed=1 loops=0 len=8000 "
            "vol=0.90 cues=1",
        )

    def test_the_audio_is_asked_for_before_the_cue_file_is_opened(self) -> None:
        """v5.68, and it is worth a test of its own because it is 200 ms of
        silence on the porch. v5.67 read the manifest AND the cue file before
        it called `sfx`, and request-to-audible went from ~650 ms to ~855 (the
        ring's scene_start→sound: 400, then 604). The pipeline needs ~400 ms to
        spin up whatever we do, so the cue file is opened AFTER the play call,
        beside that wait rather than in front of it.

        `start:` is scene_run's own order; `begin:` is step one alone, and its
        `cues=0` is the proof that no cue file was touched before the audio.
        """
        self.write_card()
        lines = self.run_ops("start:vigil")
        self.assertLess(
            lines.index("audio 01_vigil"), lines.index("cues loaded=1 count=1")
        )
        # Step one on its own has every number the play call needs and has
        # read nothing else.
        first, state = self.run_ops("begin:vigil")
        self.assertEqual(first, "begin known=1")
        self.assertIn("audio=01_vigil", state)
        self.assertIn("vol=0.45", state)
        self.assertIn("cues=0", state)
        # And step two is what fills them in, armed from the REQUEST instant.
        self.assertEqual(
            self.run_ops("begin:vigil", "cues")[-1], "cues loaded=1 count=1"
        )

    def test_a_looping_scene_reruns_without_reopening_the_manifest(self) -> None:
        """A looping scene re-executes itself every length_ms with the same
        id, and its row cannot have changed — nothing writes the card while
        the show runs. So the re-run keeps the row it already has. Proven by
        DELETING the manifest between the two starts: a re-run that still
        knows vigil's numbers never opened the file, and a different id after
        that is honestly unknown.
        """
        self.write_card()
        lines = self.run_ops("start:vigil", "rm:show.man", "start:vigil", "start:storm")
        self.assertEqual(lines[4], "rm show.man ok")
        # The re-run still knows vigil's numbers, from a file that is gone.
        self.assertEqual(lines[5], "begin known=1")
        self.assertIn("running=vigil", lines[8])
        self.assertIn("len=30000", lines[8])
        # And it is a reuse of ONE row, not a cached manifest: a different id
        # is honestly unknown.
        self.assertEqual(lines[9], "begin known=0")

    def test_the_length_is_the_condition_that_replaced_573_delays(self) -> None:
        """One `wait_until finished()` instead of a delay per cue plus a tail
        delay. The clock starts when the speaker is heard, so `finished` is
        measured from THERE and not from the start of the script."""
        self.write_card()
        ops = ["begin:storm", "clock:5000000"]
        self.assertEqual(self.one(*ops, "fin:5000000"), "fin 0")
        self.assertEqual(self.one(*ops, "fin:12999999"), "fin 0")
        self.assertEqual(self.one(*ops, "fin:13000000"), "fin 1")  # +8000 ms
        self.assertEqual(self.one(*ops, "fin:99000000"), "fin 1")

    def test_nothing_running_is_finished_so_a_wait_cannot_hang(self) -> None:
        """`wait_until` with no scene armed must fall through immediately —
        an unknown id took the fallback path and must not then park the
        runner on a condition that can never come true."""
        self.write_card()
        self.assertEqual(self.one("fin:0"), "fin 1")
        self.assertEqual(self.one("begin:nosuch", "fin:0"), "fin 1")
        self.assertEqual(self.one("begin:storm", "stop", "fin:0"), "fin 1")

    def test_j2_a_scene_that_ends_gives_its_cues_back(self) -> None:
        """J2 (grade report 2026-09-17 pm): `wait_until finished` had no else
        branch until v5.69, so a non-looping scene that simply ended left
        `g_armed` true and its PSRAM cue blob allocated — /api/status kept
        reporting `cues` and naming the scene, the zones held the last look
        all night, and the desk and the radio suppressed their own light
        frames because of that number. The else branch runs `cues_end`, which
        routes through castle_scenes::stop()."""
        self.write_card()
        ops = ["start:storm", "clock:0"]
        # Before the length is up nothing has changed, cue blob included.
        self.assertEqual(self.one(*ops, "fin:7999999"), "fin 0")
        self.assertEqual(self.one(*ops, "active"), "active 1")
        # The length is up, and the script's tail hands everything back.
        lines = self.run_ops(*ops, "fin:8000000", "end")
        self.assertEqual(lines[-2], "fin 1")
        self.assertEqual(lines[-1], "end active=0 armed=0 cues=0 running=")

    def test_j2_a_looping_scene_that_ends_keeps_its_cues(self) -> None:
        """The other branch: a loop re-executes the script with the running id
        and every number stays exactly as it was, because begin() reuses the
        row already in hand (no card read) and the blob is reloaded, not
        freed. Getting this wrong would make Vigil restart from a dark porch
        every thirty seconds."""
        self.write_card()
        lines = self.run_ops("start:vigil", "clock:0", "fin:30000000", "end")
        self.assertEqual(lines[-2], "fin 1")
        self.assertEqual(lines[-1], "end active=1 armed=1 cues=1 running=vigil")

    def test_stop_forgets_the_scene_and_gives_the_cues_back(self) -> None:
        self.write_card()
        self.assertEqual(
            self.one("start:vigil", "stop"),
            "stop running= audio= armed=0 loops=0 len=0 vol=0.00 cues=0",
        )

    def test_a_restart_mid_scene_replaces_the_state_it_does_not_add_to_it(
        self,
    ) -> None:
        """A scene re-fired while it is running (mode: restart, or the PIR
        firing during the playlist) must leave exactly one scene armed with
        exactly one cue blob — begin() stops before it loads."""
        self.write_card()
        lines = self.run_ops("start:vigil", "start:vigil", "start:storm")
        self.assertEqual(
            lines[-1],
            "state running=storm audio=02_storm armed=1 loops=0 len=8000 "
            "vol=0.90 cues=1",
        )

    def test_scene_then_raw_song_then_scene(self) -> None:
        """/api/play halts the scene and plays a card file, which may bring a
        `.cue` of its own; then a scene starts again. The failure this pins is
        one blob belonging to two owners: a scene's cues must not survive into
        the song, nor the song's into the next scene."""
        self.write_card()
        song = dict(SCENES[1], id="ghostbusters", duration_ms=200000)
        cues = [{"t": t, "op": "strike"} for t in range(0, 20000, 500)]
        (self.card / "ghostbusters.cue").write_bytes(cue_file.encode(song, cues, ZONES))
        lines = self.run_ops(
            "start:vigil",
            "stop",  # what run_scene("halt") does
            "raw:ghostbusters.mp3",
            "start:storm",
        )
        self.assertEqual(lines[5], f"raw loaded=1 cues={len(cues)}")
        # The song's 40 cues are gone; storm's one is loaded and armed=1 names
        # storm, not the song.
        self.assertIn("running=storm", lines[-1])
        self.assertIn("cues=1", lines[-1])

    def test_a_scene_track_never_resolves_a_cue_file_by_track_name(self) -> None:
        """The raw path deliberately refuses a name with a slash: a scene's
        audio is `scenes/01_vigil.mp3`, whose cue file is named after the
        SCENE and is the runner's to load. Left to path_for it would have
        resolved to /sd/scenes/01_vigil.cue, which nothing writes."""
        self.write_card()
        self.assertEqual(
            self.run_ops("raw:scenes/01_vigil.mp3"), ["raw loaded=0 cues=0"]
        )
        self.assertEqual(self.run_ops("raw:../escape.mp3"), ["raw loaded=0 cues=0"])


class TestTheCueFileHalf(SceneRunnerCase):
    def test_a_scene_whose_cue_file_is_gone_still_starts(self) -> None:
        """The partial failure worth getting right: the scene EXISTS, its
        audio will play, and the caller wears the built-in fallback look and
        says so (castle_scenes.yaml, /api/status `missing`). A refusal here
        would be a silent porch for a card that is only half-published."""
        self.write_card(cues=False)
        lines = self.run_ops("start:vigil")
        self.assertEqual(lines[0], "begin known=1")
        # The audio was still asked for — that is the whole point of the
        # partial failure — and only the cue file came back empty.
        self.assertEqual(lines[1], "audio 01_vigil")
        self.assertEqual(lines[2], "cues loaded=0 count=0")
        self.assertIn("armed=1", lines[-1])
        self.assertIn("len=30000", lines[-1])
        self.assertIn("cues=0", lines[-1])  # nothing to walk

    def test_the_base_look_comes_out_of_the_cue_file(self) -> None:
        """apply_base is the first records of the file — the successor of the
        script's first lambda of zone assignments."""
        self.write_card()
        self.assertEqual(self.one("start:vigil", "base"), "base 1/0.40 1/0.40 2/0.50")
        self.assertEqual(self.one("start:storm", "base"), "base 9/1.00 9/1.00 0/1.00")

    def test_j7_a_cue_file_larger_than_one_read_chunk_loads_whole(self) -> None:
        """J7 (grade report 2026-09-17): load() used to read the whole body in
        one fread on the main loop — up to 512 KB of SPI with the scheduler
        gone for the duration. It reads kReadChunk (32 KB) at a time with a
        yield between pieces now, and the bytes must be identical: an off-by-
        one in that loop would truncate the tail of a long song's show, which
        is the half nobody watches during a test.
        """
        self.write_card()
        # A Record is 16 bytes, so 4,000 cues is well past the 32 KB chunk and
        # over three of them; 40 is comfortably under one.
        for n in (40, 4000):
            with self.subTest(cues=n):
                big = dict(SCENES[1], duration_ms=400000)
                cues = [
                    {"t": 10 * i, "op": "strike", "intensity": 0.5} for i in range(n)
                ]
                blob = cue_file.encode(big, cues, ZONES)
                (self.card / "storm.cue").write_bytes(blob)
                self.assertGreater(len(blob), 32768 if n == 4000 else 0)
                state = self.run_ops("start:storm")[-1]
                self.assertIn(f"cues={n}", state)
                # And the LAST record is really there: tick past every one of
                # them and the count applied adds up.
                ops = ["start:storm"] + [f"tick:{16000 * k}" for k in range(1, n + 1)]
                applied = sum(
                    int(ln.split()[1]) for ln in self.run_ops(*ops) if ln[0] == "t"
                )
                self.assertEqual(applied, n)

    def test_j7_a_level_over_one_is_clamped_before_it_reaches_the_strips(
        self,
    ) -> None:
        """J7: the level fields are a percent in a u8, and 255 means "keep".
        Every other value above 100 had no meaning and was divided by 100
        anyway — 254 arrived at the render loop as 2.54, which saturates every
        channel of the zone and washes the scene white. cue_file.py cannot
        write such a byte; a corrupt sector or a hand-edited file can, so the
        clamp is on the reading side where it belongs.
        """
        self.write_card()
        blob = bytearray(cue_file.encode(SCENES[0], [], ZONES))
        # Zone records follow the header; `level` is the second byte of each
        # (cue_file.ZONE = "<BBbBBBH": effect, level, center, ...).
        at = cue_file.HEADER.size
        for i, raw in enumerate((254, 255, 100)):
            blob[at + cue_file.ZONE.size * i + 1] = raw
        (self.card / "vigil.cue").write_bytes(bytes(blob))
        self.assertEqual(self.one("start:vigil", "base"), "base 1/1.00 1/1.00 2/1.00")

    def test_a_set_cue_level_is_clamped_the_same_way(self) -> None:
        """The other place a level is read: a `set` record's own level, which
        overrides the base one mid-scene. 255 is "keep" there too, so the
        clamp has to sit inside the not-keep branch and not in front of it."""
        self.write_card()
        scene = dict(SCENES[1], cues=[{"t": 0, "op": "set", "zone": "towerL",
                                       "effect": "candle", "level": 1.0}])  # fmt: skip
        blob = bytearray(cue_file.encode(scene, scene["cues"], ZONES))
        # The single record follows the three zone records; SET is
        # "<IBBBB8x": t, op, mask, effect, level.
        at = cue_file.HEADER.size + cue_file.ZONE.size * 3
        blob[at + 7] = 254
        (self.card / "storm.cue").write_bytes(bytes(blob))
        lines = self.run_ops("start:storm", "base", "tick:16000", "zones")
        self.assertEqual(lines[-2], "tick 1")
        # towerL took the cue's effect (candle = 1) and its level came through
        # clamped, not as 2.54.
        self.assertEqual(lines[-1], "zones 1/1.00 9/1.00 0/1.00")


if __name__ == "__main__":
    unittest.main()
