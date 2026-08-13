"""Do the previewer and the firmware agree about what the show looks like?

The project's central claim is that the browser cue desk predicts the device.
Two separate generators read scenes/scenes.yaml — tools/gen_esphome.py for the
ESPHome scripts and tools/gen_previewer.py for the browser — and each has its
own copy of the pulse-merging logic. Nothing forces them to stay in step.

A divergence here is the worst bug this project can have, because it is
invisible: both sides run, neither errors, and the preview quietly stops being
a preview. These tests are the thing that would notice.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_esphome as ge        # noqa: E402
import gen_previewer as gp      # noqa: E402
import yaml                     # noqa: E402

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]
ZIDS = [z["id"] for z in ZONES]

# One scene exercising every pulse routing the format allows: a zone-less
# stream (whole chain), a single named zone, an explicit zone list, and a
# round-robin — each with its own colour, decay and intensity.
PULSE_SCENE = {
    "id": "parity", "name": "Parity", "kind": "triggered",
    "duration_ms": 8000, "volume": 0.7,
    "base": {"towerL": "candle", "towerR": "spirit", "door": "ember"},
    "pulse": [
        {"synth": "toll", "intensity": 0.5, "decay": 0.97,
         "color": [0.66, 0.08, 1.0, 0.06]},
        {"synth": "heartbeat", "zone": "door", "intensity": 0.85,
         "decay": 0.82, "color": [1.0, 0.03, 0.01, 0.0]},
        {"synth": "heartbeat", "zones": ["towerL", "towerR"],
         "intensity": 0.10, "decay": 0.88, "color": [1.0, 0.05, 0.02, 0.0]},
        {"synth": "whispers", "zones": ["towerL", "towerR"], "alternate": True,
         "intensity": 0.34, "decay": 0.94, "color": [0.24, 1.0, 0.38, 0.0]},
    ],
}

MARKERS = {"parity": {
    "toll": [[0, 1.0], [4200, 0.75]],
    "heartbeat": [[0, 1.0], [153, 0.55], [820, 0.91], [1600, 0.4]],
    "whispers": [[2600, 0.806], [3537, 0.994], [4100, 0.5], [5000, 1.0]],
}}


def esphome_strikes(scene: dict, markers: dict) -> list[tuple]:
    """Pulse strikes as gen_esphome will write them into the ESPHome script."""
    return sorted(
        (c["t"], tuple(c["targets"] or ZIDS), c["intensity"],
         tuple(float(v) for v in c["color"]), c["decay"])
        for c in ge.pulse_cues(scene, markers))


def previewer_strikes(scene: dict, markers: dict, idx: int = 1) -> list[tuple]:
    """The same strikes as gen_previewer will hand to the browser."""
    cues = gp.to_previewer(scene, idx, "", markers)["cues"]
    return sorted(
        (c["t"], tuple(c.get("targets") or ZIDS), c["intensity"],
         tuple(float(v) for v in c["color"]), c["decay"])
        for c in cues if c["op"] == "strike" and "intensity" in c)


class TestPulseParity(unittest.TestCase):
    """The contract: one pulse block, two generators, identical strike cues."""

    def test_every_routing_mode_agrees(self) -> None:
        """Zone-less, single-zone, multi-zone and alternating streams together.

        Each mode is a separate branch duplicated in both files, so this is
        where a one-sided edit shows up. Comparing the whole set at once also
        catches a stream being dropped or emitted twice.
        """
        a = esphome_strikes(PULSE_SCENE, MARKERS)
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
        self.assertEqual(esphome_strikes(s, MARKERS), previewer_strikes(s, MARKERS))

    def test_round_robin_lands_on_the_same_zone_at_the_same_time(self) -> None:
        """If the two sides indexed differently, the towers would swap.

        Visually plausible on both sides in isolation, and only wrong when you
        compare the browser to the wall.
        """
        s = dict(PULSE_SCENE, pulse=[PULSE_SCENE["pulse"][3]])
        pairs = [(t, z) for t, z, *_ in esphome_strikes(s, MARKERS)]
        self.assertEqual(pairs, [(2600, ("towerL",)), (3537, ("towerR",)),
                                 (4100, ("towerL",)), (5000, ("towerR",))])
        self.assertEqual(pairs, [(t, z) for t, z, *_ in previewer_strikes(s, MARKERS)])

    def test_velocity_scaling_is_rounded_identically(self) -> None:
        """Both round to 3 places; a float-vs-rounded mismatch would drift apart."""
        s = dict(PULSE_SCENE, pulse=[{"synth": "whispers", "intensity": 0.34}])
        got = [c[2] for c in esphome_strikes(s, MARKERS)]
        self.assertEqual(got, [0.274, 0.338, 0.17, 0.34])
        self.assertEqual(got, [c[2] for c in previewer_strikes(s, MARKERS)])

    def test_default_colour_and_decay_match(self) -> None:
        """The defaults are written out separately in each file as literals."""
        s = dict(PULSE_SCENE, pulse=[{"synth": "toll"}])
        self.assertEqual(esphome_strikes(s, MARKERS)[0][2:],
                         previewer_strikes(s, MARKERS)[0][2:])
        self.assertEqual(ge.WHITE, [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(ge.DEFAULT_DECAY, 0.90)

    def test_a_missing_synth_produces_nothing_on_either_side(self) -> None:
        s = dict(PULSE_SCENE, pulse=[{"synth": "absent", "zone": "door"}])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(esphome_strikes(s, MARKERS), [])
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
                    a = [(c["t"], tuple(c["targets"] or zids), c["intensity"])
                         for c in ge.pulse_cues(scene, markers)]
                b = [(c["t"], tuple(c.get("targets") or zids), c["intensity"])
                     for c in gp.to_previewer(scene, i, raw, markers)["cues"]
                     if c["op"] == "strike" and "intensity" in c]
                self.assertEqual(sorted(a), sorted(b))


class TestTimelineParity(unittest.TestCase):
    """Same cues is not enough — they have to happen at the same moment."""

    def emitted_times(self, scene: dict, markers: dict) -> list[int]:
        """Replay the ESPHome script's delays to recover absolute cue times."""
        then = yaml.safe_load(
            "script:\n" + "\n".join(ge.emit_scene(scene, ZONES, 1, markers))
        )["script"][0]["then"]
        start = next(i for i, st in enumerate(then)
                     if isinstance(st.get("script.execute"), dict)
                     and st["script.execute"].get("id") == "sfx")
        times, t = [], 0
        for st in then[start + 1:]:
            if "delay" in st:
                t += int(str(st["delay"]).removesuffix("ms"))
            elif "lambda" in st:
                times.append(t)
        return times

    def test_delays_replay_to_the_previewers_absolute_times(self) -> None:
        """The delta encoding is the only place the two formats really differ.

        The previewer keeps absolute milliseconds; ESPHome only has `delay:`.
        Summing the emitted deltas back has to reproduce exactly the timeline
        the browser plays, mixed cues and pulses included.
        """
        s = dict(PULSE_SCENE, cues=[
            {"t": 80, "op": "strike", "note": "lightning"},
            {"t": 3000, "op": "set", "zone": "door", "effect": "blood"}])
        with contextlib.redirect_stdout(io.StringIO()):
            emitted = self.emitted_times(s, MARKERS)
        preview = [c["t"] for c in gp.to_previewer(s, 1, "", MARKERS)["cues"]
                   if c["bus"] == "LED"]
        self.assertEqual(emitted, sorted(preview))

    def test_simultaneous_cues_do_not_collapse_into_one(self) -> None:
        """Two zones struck on the same beat must stay two events on both sides."""
        s = dict(PULSE_SCENE, pulse=[
            {"synth": "heartbeat", "zone": "door"},
            {"synth": "heartbeat", "zones": ["towerL"]}])
        self.assertEqual(len(self.emitted_times(s, MARKERS)), 8)
        self.assertEqual(len(previewer_strikes(s, MARKERS)), 8)



class TestGenPreviewerMain(unittest.TestCase):
    """The whole splice, on tempfiles: does a real page come out the far end?"""

    TEMPLATE = ("<style>/* @STYLES here */</style>\n"
                "<script>\n"
                "  // @GEN-DATA-START\n"
                "  window.CASTLE_GEN = {};\n"
                "  // @GEN-DATA-END\n"
                "  /* @BUNDLE here */\n"
                "</script>\n")

    DOC = {"zones": ZONES, "scenes": [
        {**PULSE_SCENE, "id": "one"},
        {"id": "two", "name": "Two", "kind": "ambient", "duration_ms": 100,
         "base": {"door": "candle"}},
    ]}

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {k: getattr(gp, k) for k in
                       ("ROOT", "SRC", "MARKERS_FILE", "TEMPLATE", "HTML",
                        "AUDIO", "WEB", "BUNDLE", "STYLES", "subprocess")}
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
        gp.subprocess = types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout="", stderr=""))

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            setattr(gp, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gp.main(), 0)
        return gp.HTML.read_text()

    def gen(self, html: str) -> dict:
        body = html.split(gp.START, 1)[1].split(gp.END, 1)[0]
        return json.loads(body.split("=", 1)[1].rsplit(";", 1)[0])

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
        self.assertNotIn("two", data["audio"])   # no file rendered yet

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
        with self.assertRaises(SystemExit) as cm, \
                contextlib.redirect_stdout(io.StringIO()):
            gp.main()
        self.assertIn("markers not found", str(cm.exception))
        self.assertFalse(gp.HTML.exists())

    def test_the_page_is_only_written_once_everything_succeeded(self) -> None:
        """A partial write leaves a stale-looking page that loads and lies."""
        gp.STYLES.unlink()
        with self.assertRaises(FileNotFoundError), \
                contextlib.redirect_stdout(io.StringIO()):
            gp.main()
        self.assertFalse(gp.HTML.exists())


def dynamic_strikes(side, scene: dict, markers: dict) -> list[tuple]:
    """Strikes including the per-hit dynamics fields (pixels, ms, colour)."""
    if side == "esphome":
        cues = ge.pulse_cues(scene, markers)
    else:
        cues = [c for c in gp.to_previewer(scene, 1, "", markers)["cues"]
                if c["op"] == "strike"]
    return sorted(
        (c["t"], tuple(c.get("targets") or ZIDS), c["intensity"],
         tuple(float(v) for v in c["color"]),
         c.get("pixels", "all"), c.get("ms", 120))
        for c in cues)


class TestPulseDynamicsParity(unittest.TestCase):
    """The per-hit dynamics: colour blend, velocity masks, boost, ms.

    Each is arithmetic duplicated across gen_esphome.py, gen_previewer.py and
    web/src/track_lights.ts. These pin the two Python copies to each other and
    to the exact numbers the TypeScript copy is tested against, so all three
    meet at the same values.
    """

    DYN = {"synth": "heartbeat", "zone": "door", "intensity": 0.6,
           "color": [1.0, 0.0, 0.0, 0.0], "color_hot": [1.0, 0.5, 0.0, 0.4],
           "pixels_by_vel": True, "ms": 110,
           "boost_at": 0.85, "boost_targets": ["towerL", "towerR"]}

    def scene(self, **over) -> dict:
        return dict(PULSE_SCENE, pulse=[dict(self.DYN, **over)])

    def test_colour_blends_by_velocity_identically(self) -> None:
        # heartbeat velocities: 1.0, 0.55, 0.91, 0.4
        a = dynamic_strikes("esphome", self.scene(), MARKERS)
        b = dynamic_strikes("previewer", self.scene(), MARKERS)
        self.assertEqual(a, b)
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 0.5, 0.0, 0.4))          # vel 1.0: hot
        self.assertEqual(by_t[153], (1.0, 0.275, 0.0, 0.22))     # vel 0.55
        self.assertEqual(by_t[1600], (1.0, 0.2, 0.0, 0.16))      # vel 0.40

    def test_velocity_picks_the_same_mask(self) -> None:
        masks = {t: p for t, _z, _i, _c, p, _m
                 in dynamic_strikes("esphome", self.scene(), MARKERS)}
        # vel 0.40 sits ON the centre/scatter edge; both sides use `<`.
        self.assertEqual(masks, {0: "all", 153: "scatter",
                                 820: "all", 1600: "scatter"})
        self.assertEqual(masks, {t: p for t, _z, _i, _c, p, _m
                                 in dynamic_strikes("previewer", self.scene(), MARKERS)})

    def test_soft_hits_land_on_the_centre(self) -> None:
        m = {"parity": {"heartbeat": [[100, 0.2]]}}
        for side in ("esphome", "previewer"):
            (_t, _z, _i, _c, pixels, _ms), = dynamic_strikes(side, self.scene(), m)
            self.assertEqual(pixels, "center")

    def test_boost_spills_onto_the_extra_zones(self) -> None:
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), MARKERS)}
            # vel 1.0 and 0.91 clear boost_at 0.85; 0.55 and 0.40 do not.
            self.assertEqual(zones[0], ("door", "towerL", "towerR"))
            self.assertEqual(zones[820], ("door", "towerL", "towerR"))
            self.assertEqual(zones[153], ("door",))
            self.assertEqual(zones[1600], ("door",))

    def test_boost_does_not_fire_without_targets(self) -> None:
        s = self.scene()
        del s["pulse"][0]["boost_targets"]
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, MARKERS)}
            self.assertEqual(zones[0], ("door",))

    def test_ms_reaches_both_sides(self) -> None:
        for side in ("esphome", "previewer"):
            self.assertEqual({m for *_rest, m in dynamic_strikes(side, self.scene(), MARKERS)},
                             {110})

    def test_colour_cycle_agrees_hit_for_hit(self) -> None:
        """`colors:` cycles by hit index, identically on both sides."""
        cyc = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = self.scene(colors=cyc)
        del s["pulse"][0]["color_hot"]      # bare cycle, no blend
        a = dynamic_strikes("esphome", s, MARKERS)
        b = dynamic_strikes("previewer", s, MARKERS)
        self.assertEqual(a, b)
        # heartbeat markers in time order: t=0, 153, 820, 1600 — cycle wraps.
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(by_t[153], (0.0, 1.0, 0.0, 0.0))
        self.assertEqual(by_t[820], (0.0, 0.0, 1.0, 0.0))
        self.assertEqual(by_t[1600], (1.0, 0.0, 0.0, 0.0))

    def test_colour_cycle_blends_toward_hot(self) -> None:
        cyc = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = self.scene(colors=cyc, color_hot=[1.0, 1.0, 0.0, 0.0])
        a = dynamic_strikes("esphome", s, MARKERS)
        self.assertEqual(a, dynamic_strikes("previewer", s, MARKERS))
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 1.0, 0.0, 0.0))       # vel 1.0 -> hot
        self.assertEqual(by_t[153], (0.55, 0.55, 0.45, 0.0))  # vel .55 from blue

    def test_plain_streams_are_untouched(self) -> None:
        """A stream without the new fields renders exactly as before."""
        a = esphome_strikes(PULSE_SCENE, MARKERS)
        b = previewer_strikes(PULSE_SCENE, MARKERS)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 14)


if __name__ == "__main__":
    unittest.main(verbosity=2)
