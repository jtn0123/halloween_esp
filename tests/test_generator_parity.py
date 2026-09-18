"""Do the previewer and the firmware agree about what the show looks like?

The project's central claim is that the browser cue desk predicts the device.
Two separate generators read scenes/scenes.yaml — tools/gen_scene_cards.py for
the card the castle plays and tools/gen_previewer.py for the browser — and each
has its own copy of the pulse-merging logic. Nothing forces them to stay in
step.

A divergence here is the worst bug this project can have, because it is
invisible: both sides run, neither errors, and the preview quietly stops being
a preview. These tests are the thing that would notice.

Until v5.67 the device side of the comparison was the emitted ESPHome script,
and two of its facts were about the shape of that script rather than the show:
the deltas that `delay:` forced cue times into, and PULSE_CAP, which thinned a
dense track to 200 hits on BOTH sides so that the desk still matched a device
that could not afford the rest. The device side is a cue file on the card now.
Neither side thins, and the times are absolute on both, so the comparison is
direct — and it runs all the way through the real encoder, because a u8 colour
channel is the one place a number can still change between the two.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import gen_esphome as ge
import gen_previewer as gp
import gen_scene_cards as gc
import yaml

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]
ZIDS = [z["id"] for z in ZONES]

# One scene exercising every pulse routing the format allows: a zone-less
# stream (whole chain), a single named zone, an explicit zone list, and a
# round-robin — each with its own colour, decay and intensity.
PULSE_SCENE = {
    "id": "parity",
    "name": "Parity",
    "kind": "triggered",
    "duration_ms": 8000,
    "volume": 0.7,
    "base": {"towerL": "candle", "towerR": "spirit", "door": "ember"},
    "pulse": [
        {
            "synth": "toll",
            "intensity": 0.5,
            "decay": 0.97,
            "color": [0.66, 0.08, 1.0, 0.06],
        },
        {
            "synth": "heartbeat",
            "zone": "door",
            "intensity": 0.85,
            "decay": 0.82,
            "color": [1.0, 0.03, 0.01, 0.0],
        },
        {
            "synth": "heartbeat",
            "zones": ["towerL", "towerR"],
            "intensity": 0.10,
            "decay": 0.88,
            "color": [1.0, 0.05, 0.02, 0.0],
        },
        {
            "synth": "whispers",
            "zones": ["towerL", "towerR"],
            "alternate": True,
            "intensity": 0.34,
            "decay": 0.94,
            "color": [0.24, 1.0, 0.38, 0.0],
        },
    ],
}

MARKERS = {
    "parity": {
        "toll": [[0, 1.0], [4200, 0.75]],
        "heartbeat": [[0, 1.0], [153, 0.55], [820, 0.91], [1600, 0.4]],
        "whispers": [[2600, 0.806], [3537, 0.994], [4100, 0.5], [5000, 1.0]],
    }
}


def device_strikes(
    scene: dict[str, Any], markers: dict[str, Any]
) -> list[tuple[Any, ...]]:
    """Pulse strikes as the card path will carry them — every hit, at full
    precision, before cue_file quantises them. `pd.thin_pulses` used to wrap
    this: the script could afford 200 actions, so the desk had to drop the
    same ones. Neither does (docs/PARITY.md, v5.67)."""
    return sorted(
        (
            c["t"],
            tuple(c["targets"] or ZIDS),
            c["intensity"],
            tuple(float(v) for v in c["color"]),
            c["decay"],
        )
        for c in ge.pulse_cues(scene, markers)
    )


def previewer_strikes(
    scene: dict[str, Any], markers: dict[str, Any], idx: int = 1
) -> list[tuple[Any, ...]]:
    """The same strikes as gen_previewer will hand to the browser."""
    cues = gp.to_previewer(scene, idx, "", markers)["cues"]
    return sorted(
        (
            c["t"],
            tuple(c.get("targets") or ZIDS),
            c["intensity"],
            tuple(float(v) for v in c["color"]),
            c["decay"],
        )
        for c in cues
        if c["op"] == "strike" and "intensity" in c
    )


class TestPulseParity(unittest.TestCase):
    """The contract: one pulse block, two generators, identical strike cues."""

    def test_every_routing_mode_agrees(self) -> None:
        """Zone-less, single-zone, multi-zone and alternating streams together.

        Each mode is a separate branch duplicated in both files, so this is
        where a one-sided edit shows up. Comparing the whole set at once also
        catches a stream being dropped or emitted twice.
        """
        a = device_strikes(PULSE_SCENE, MARKERS)
        b = previewer_strikes(PULSE_SCENE, MARKERS)
        self.assertEqual(len(a), 14)
        self.assertEqual(a, b)

    def test_they_agree_on_which_zones_a_zone_less_stream_hits(self) -> None:
        """gen_esphome says targets=None, gen_previewer omits the key entirely.

        Two different spellings of "the whole chain". They only mean the same
        thing because the emitter and the browser each expand the absent value
        to every zone — assert the expansion, not the spelling.
        """
        s = dict(PULSE_SCENE, pulse=[{"synth": "toll"}])
        self.assertIsNone(ge.pulse_cues(s, MARKERS)[0]["targets"])
        self.assertNotIn("targets", gp.to_previewer(s, 1, "", MARKERS)["cues"][0])
        self.assertEqual(device_strikes(s, MARKERS), previewer_strikes(s, MARKERS))

    def test_round_robin_lands_on_the_same_zone_at_the_same_time(self) -> None:
        """If the two sides indexed differently, the towers would swap.

        Visually plausible on both sides in isolation, and only wrong when you
        compare the browser to the wall.
        """
        s = dict(PULSE_SCENE, pulse=[PULSE_SCENE["pulse"][3]])  # type: ignore[index]  # heterogeneous scene dict
        pairs = [(t, z) for t, z, *_ in device_strikes(s, MARKERS)]
        self.assertEqual(
            pairs,
            [
                (2600, ("towerL",)),
                (3537, ("towerR",)),
                (4100, ("towerL",)),
                (5000, ("towerR",)),
            ],
        )
        self.assertEqual(pairs, [(t, z) for t, z, *_ in previewer_strikes(s, MARKERS)])

    def test_velocity_scaling_is_rounded_identically(self) -> None:
        """Both round to 3 places; a float-vs-rounded mismatch would drift apart."""
        s = dict(PULSE_SCENE, pulse=[{"synth": "whispers", "intensity": 0.34}])
        got = [c[2] for c in device_strikes(s, MARKERS)]
        self.assertEqual(got, [0.274, 0.338, 0.17, 0.34])
        self.assertEqual(got, [c[2] for c in previewer_strikes(s, MARKERS)])

    def test_default_colour_and_decay_match(self) -> None:
        """The defaults are written out separately in each file as literals."""
        s = dict(PULSE_SCENE, pulse=[{"synth": "toll"}])
        self.assertEqual(
            device_strikes(s, MARKERS)[0][2:], previewer_strikes(s, MARKERS)[0][2:]
        )
        self.assertEqual(ge.WHITE, [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(ge.DEFAULT_DECAY, 0.90)

    def test_a_missing_synth_produces_nothing_on_either_side(self) -> None:
        s = dict(PULSE_SCENE, pulse=[{"synth": "absent", "zone": "door"}])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(device_strikes(s, MARKERS), [])
        self.assertEqual(previewer_strikes(s, MARKERS), [])

    def test_the_real_scenes_file_agrees_scene_for_scene(self) -> None:
        """The synthetic cases prove the branches; this proves the actual show.

        Read-only: it loads the shipped scenes.yaml and markers.json and
        compares, so a future edit to either generator is caught against real
        data as well as against a fixture.
        """
        raw = (ROOT / "scenes" / "scenes.yaml").read_text()
        doc = yaml.safe_load(raw)
        mf = ROOT / "audio" / "markers.json"
        markers = json.loads(mf.read_text()) if mf.exists() else {}
        zids = [z["id"] for z in doc["zones"]]
        for i, scene in enumerate(doc["scenes"], start=1):
            with self.subTest(scene=scene["id"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    a = [
                        (c["t"], tuple(c["targets"] or zids), c["intensity"])
                        for c in ge.pulse_cues(scene, markers)
                    ]
                b = [
                    (c["t"], tuple(c.get("targets") or zids), c["intensity"])
                    for c in gp.to_previewer(scene, i, raw, markers)["cues"]
                    if c["op"] == "strike" and "intensity" in c
                ]
                self.assertEqual(sorted(a), sorted(b))


class TestTimelineParity(unittest.TestCase):
    """Same cues is not enough — they have to happen at the same moment.

    And this is the class that runs the whole device encoder, so it is the
    permanent card-vs-desk gate: a record is read off the card by
    firmware/castle_cues.h with these exact bytes in it.
    """

    def record_times(self, scene: dict[str, Any], markers: dict[str, Any]) -> list[int]:
        """Cue times as the device will read them — out of the encoded file.

        Was `emitted_times`, which summed the emitted `delay:` chain back into
        absolute milliseconds because ESPHome had no other way to express a
        timeline. The file carries the author's own millisecond in a u32, so
        what is left to check is that the encoder neither reorders nor loses
        one, and that a value over 65 s did not wrap (the u32 is the reason
        the field is not a u16; The Ballad is 54 s and Citizens is longer).
        """
        cues = gc.scene_cues(scene, markers)
        doc = cue_file.decode(cue_file.encode(scene, cues, ZIDS))
        return [r["t"] for r in doc["records"]]

    def test_the_file_carries_the_previewers_absolute_times(self) -> None:
        """Was `test_delays_replay_to_the_previewers_absolute_times`.

        Mixed authored cues and pulses, because the merge order is the thing
        that used to go wrong: the previewer sorts by time and the device side
        has to arrive at the same sequence, or a `set` lands after the strike
        it was meant to precede.
        """
        s = dict(
            PULSE_SCENE,
            cues=[
                {"t": 80, "op": "strike", "note": "lightning"},
                {"t": 3000, "op": "set", "zone": "door", "effect": "blood"},
            ],
        )
        with contextlib.redirect_stdout(io.StringIO()):
            got = self.record_times(s, MARKERS)
        preview = [
            c["t"]
            for c in gp.to_previewer(s, 1, "", MARKERS)["cues"]
            if c["bus"] == "LED"
        ]
        self.assertEqual(got, sorted(preview))

    def test_simultaneous_cues_do_not_collapse_into_one(self) -> None:
        """Two zones struck on the same beat must stay two events on both sides."""
        s = dict(
            PULSE_SCENE,
            pulse=[
                {"synth": "heartbeat", "zone": "door"},
                {"synth": "heartbeat", "zones": ["towerL"]},
            ],
        )
        self.assertEqual(len(self.record_times(s, MARKERS)), 8)
        self.assertEqual(len(previewer_strikes(s, MARKERS)), 8)

    def test_a_late_cue_survives_the_encoding_that_a_u16_would_have_wrapped(
        self,
    ) -> None:
        """Citizens runs past 90 s. A truncated time is a cue that fires in the
        first seconds of the scene instead of near its end — visible, and
        impossible to attribute to an encoder without a test that says so."""
        s = dict(
            PULSE_SCENE,
            duration_ms=200_000,
            pulse=[],
            cues=[{"t": 199_999, "op": "strike"}],
        )
        self.assertEqual(self.record_times(s, MARKERS), [199_999])

    def test_the_encoded_numbers_survive_to_the_desks_precision(self) -> None:
        """The one place a number CAN change between the two sides now.

        A record stores intensity as intensity*1000 in a u16 and each colour
        channel as a percent in a u8, so the desk's 0.274 arrives as 0.274 and
        its 0.66 as 0.66 — but a channel authored to three places would not.
        Assert the quantum, so that the day someone needs finer colour they
        find this rather than a slightly-wrong porch.
        """
        s = dict(PULSE_SCENE, pulse=[PULSE_SCENE["pulse"][0]])  # type: ignore[index]  # heterogeneous scene dict
        cues = gc.scene_cues(s, MARKERS)
        doc = cue_file.decode(cue_file.encode(s, cues, ZIDS))
        want = previewer_strikes(s, MARKERS)
        self.assertEqual(len(doc["records"]), len(want))
        for rec, (t, _z, amt, col, dec) in zip(doc["records"], want, strict=True):
            self.assertEqual(rec["t"], t)
            self.assertAlmostEqual(rec["intensity"], amt, delta=1e-3)
            self.assertAlmostEqual(rec["decay"], dec, delta=1e-4)
            for a, b in zip(rec["color"], col, strict=True):
                self.assertAlmostEqual(a, b, delta=1e-2)


class TestGenPreviewerMain(unittest.TestCase):
    """The whole splice, on tempfiles: does a real page come out the far end?"""

    TEMPLATE = (
        "<style>/* @STYLES here */</style>\n"
        "<script>\n"
        "  // @GEN-DATA-START\n"
        "  window.CASTLE_GEN = {};\n"
        "  // @GEN-DATA-END\n"
        "  /* @BUNDLE here */\n"
        "</script>\n"
    )

    DOC: ClassVar[dict[str, Any]] = {
        "zones": ZONES,
        "scenes": [
            {**PULSE_SCENE, "id": "one"},
            {
                "id": "two",
                "name": "Two",
                "kind": "ambient",
                "duration_ms": 100,
                "base": {"door": "candle"},
            },
        ],
    }

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {
            k: getattr(gp, k)
            for k in (
                "ROOT",
                "SRC",
                "MARKERS_FILE",
                "TEMPLATE",
                "HTML",
                "AUDIO",
                "WEB",
                "BUNDLE",
                "STYLES",
                "subprocess",
            )
        }
        gp.ROOT = self.tmp
        gp.SRC = self.tmp / "scenes.yaml"
        gp.SRC.write_text(yaml.safe_dump(self.DOC))
        gp.MARKERS_FILE = self.tmp / "markers.json"
        gp.MARKERS_FILE.write_text(json.dumps({"one": MARKERS["parity"]}))
        gp.TEMPLATE = self.tmp / "template.html"
        gp.TEMPLATE.write_text(self.TEMPLATE)
        gp.HTML = self.tmp / "out.html"
        gp.AUDIO = self.tmp / "audio"
        gp.AUDIO.mkdir()
        gp.STYLES = self.tmp / "styles.css"
        gp.STYLES.write_text("body{}")
        gp.WEB = self.tmp / "web"
        (gp.WEB / "node_modules").mkdir(parents=True)
        gp.BUNDLE = gp.WEB / "dist" / "bundle.js"
        gp.BUNDLE.parent.mkdir(parents=True)
        gp.BUNDLE.write_text("/*js*/")
        gp.subprocess = types.SimpleNamespace(  # type: ignore[assignment]  # test double
            run=lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout="", stderr=""
            )
        )

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            setattr(gp, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gp.main(), 0)
        return gp.HTML.read_text()

    def gen(self, html: str) -> dict[str, Any]:
        body = html.split(gp.START, 1)[1].split(gp.END, 1)[0]
        return dict(json.loads(body.split("=", 1)[1].rsplit(";", 1)[0]))

    def test_scenes_land_in_the_page_in_previewer_shape(self) -> None:
        data = self.gen(self.run_main())
        self.assertEqual([s["id"] for s in data["scenes"]], ["one", "two"])
        self.assertEqual(data["scenes"][0]["file"], "01_one.mp3")
        self.assertEqual(len(data["scenes"][0]["cues"]), 14)

    def test_styles_and_bundle_replace_their_markers(self) -> None:
        html = self.run_main()
        self.assertIn("body{}", html)
        self.assertIn("/*js*/", html)
        self.assertNotIn("@STYLES", html)
        self.assertNotIn("@BUNDLE", html)

    def test_rendered_audio_is_inlined_as_a_data_uri(self) -> None:
        """The previewer must play the exact mp3 that ships, not a live synth."""
        (gp.AUDIO / "01_one.mp3").write_bytes(b"\xff\xfbfake mp3")
        data = self.gen(self.run_main())
        self.assertTrue(data["audio"]["one"].startswith("data:audio/mpeg;base64,"))
        self.assertNotIn("two", data["audio"])  # no file rendered yet

    def test_base64_backslashes_survive_the_splice(self) -> None:
        """re.sub() would read a backslash in the payload as an escape.

        Base64 has no backslashes, but the JSON encoding of the scene data
        does — every non-ASCII character in a blurb becomes a \\u escape. The
        replacement is a function for exactly this reason.
        """
        doc = dict(self.DOC)
        doc["scenes"] = [{**self.DOC["scenes"][1], "blurb": "sÉance — \\ backslash"}]
        gp.SRC.write_text(yaml.safe_dump(doc, allow_unicode=True))
        data = self.gen(self.run_main())
        self.assertEqual(data["scenes"][0]["blurb"], "sÉance — \\ backslash")

    def test_missing_data_markers_exit_rather_than_write_a_dead_page(self) -> None:
        gp.TEMPLATE.write_text("<html>no markers here</html>")
        with (
            self.assertRaises(SystemExit) as cm,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            gp.main()
        self.assertIn("markers not found", str(cm.exception))
        self.assertFalse(gp.HTML.exists())

    def test_the_page_is_only_written_once_everything_succeeded(self) -> None:
        """A partial write leaves a stale-looking page that loads and lies."""
        gp.STYLES.unlink()
        with (
            self.assertRaises(FileNotFoundError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            gp.main()
        self.assertFalse(gp.HTML.exists())
