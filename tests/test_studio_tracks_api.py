"""The Tracks panel's second wave of server behaviour (judge B, pass 1).

Delete of an in-show track taking its scene with it, --no-normalize reaching
the importer, one-line reasons on failure, a dropped file's original kept
beside the library so Re-import has something to work from, and the
castle's phone remote relayed at /remote — and the route boundary: what
the studio owns (/studio/*), what it relays (/api/*), and the one-release
aliases between them. test_studio_api.py is at the LOC cap; this file is
its continuation.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import manifest as mf
import studio
import studio_tracks
from helpers import make_click_track
from studio_case import ServerCase

TRACEBACK = ("Traceback (most recent call last):\n"
             "  File \"import_track.py\", line 1, in <module>\n"
             "    convert()\n"
             "subprocess.CalledProcessError: Command '['ffmpeg', '-v', 'quiet']'"
             " returned non-zero exit status 1.\n")


class TestImportOptions(ServerCase):
    def test_unchecked_loudness_match_reaches_the_importer(self) -> None:
        """All three import paths only ever added --normalize; an unchecked
        box was silently ignored and the row then said "normalised"."""
        with mock.patch.object(studio, "run", return_value=(True, "log")) as spy:
            self.post_json("/studio/refresh", {"id": self.WAVE_ID, "normalize": False})
        self.assertIn("--no-normalize", spy.call_args[0][0])
        self.assertNotIn("--normalize", spy.call_args[0][0])

    def test_an_absent_normalize_leaves_the_remembered_choice(self) -> None:
        with mock.patch.object(studio, "run", return_value=(True, "log")) as spy:
            self.post_json("/studio/refresh", {"id": self.WAVE_ID, "take": 3})
        argv = spy.call_args[0][0]
        self.assertNotIn("--normalize", argv)
        self.assertNotIn("--no-normalize", argv)

    def test_refresh_forwards_format_and_fades(self) -> None:
        with mock.patch.object(studio, "run", return_value=(True, "log")) as spy:
            self.post_json("/studio/refresh", {"id": self.WAVE_ID, "format": "wav",
                                            "fade_in": 0.5, "fade_out": "1"})
        argv = spy.call_args[0][0]
        for flag in ("--format", "--fade-in", "--fade-out"):
            self.assertIn(flag, argv)

    def test_a_failed_refresh_carries_one_line_reason_not_a_traceback(self) -> None:
        with mock.patch.object(studio, "run", return_value=(False, TRACEBACK)):
            code, d = self.post_json("/studio/refresh", {"id": self.WAVE_ID})
        self.assertEqual(code, 500)
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "ffmpeg failed (exit 1)")
        self.assertIn("Traceback", d["log"], "the tail stays for the curious")

    def test_an_upload_keeps_its_original_beside_the_library(self) -> None:
        """The staging copy is deleted after import, so Re-import of a
        dropped file could never work — the importer is told to keep it."""
        body = (b"--B\r\nContent-Disposition: form-data; name=\"file\"; "
                b"filename=\"clip.wav\"\r\n\r\nRIFFfake\r\n--B--\r\n")
        with mock.patch.object(studio, "run", return_value=(True, "ok")) as spy:
            code, _ = self.req("POST", "/studio/import", body, {
                "Content-Type": "multipart/form-data; boundary=B"})
        self.assertEqual(code, 200)
        self.assertIn("--keep-source", spy.call_args[0][0])


class TestSourceMissing(ServerCase):
    def test_a_gone_file_source_is_flagged_and_a_url_is_not(self) -> None:
        mf.record(self.WAVE_ID, source="file:/nowhere/at/all.wav")
        mf.record(self.WAV_ID, source="https://example.com/x")
        try:
            _, d = self.get_json("/studio/tracks")
            by = {t["id"]: t for t in d["tracks"]}
            self.assertTrue(by[self.WAVE_ID]["source_missing"])
            self.assertFalse(by[self.WAV_ID]["source_missing"])
        finally:
            mf.forget(self.WAVE_ID)
            mf.forget(self.WAV_ID)


class TestDeleteWithScene(ServerCase):
    ORIGINAL = ("scenes:\n"
                "  - id: vigil\n"
                "    duration_ms: 1000\n"
                "  - id: {tid}\n"
                "    audio_file: tracks/{tid}.wav\n"
                "  - id: storm\n"
                "    duration_ms: 2000\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.scenes = self.tmp / "scenes.yaml"
        self.scenes.write_text(self.ORIGINAL.format(tid=self.DEL_ID))
        self.track = studio.TRACKS / f"{self.DEL_ID}.wav"
        make_click_track(self.track, seconds=1.0)
        kept = studio.TRACKS / studio_tracks.SRC_DIR
        kept.mkdir(exist_ok=True)
        (kept / f"{self.DEL_ID}.wav").write_bytes(b"RIFForiginal")
        mf.record(self.DEL_ID, source=f"file:{kept / f'{self.DEL_ID}.wav'}")
        self.p_scenes = mock.patch.object(studio, "SCENES", self.scenes)
        self.p_run = mock.patch.object(studio, "run", return_value=(True, "ok"))
        self.p_scenes.start()
        self.run_spy = self.p_run.start()

    def tearDown(self) -> None:
        self.p_run.stop()
        self.p_scenes.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.track.unlink(missing_ok=True)
        mf.forget(self.DEL_ID)

    def test_plain_delete_leaves_the_scene_alone(self) -> None:
        code, d = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}")
        self.assertEqual(code, 200)
        self.assertNotIn("scene_removed", json.loads(d))
        self.assertIn(f"- id: {self.DEL_ID}", self.scenes.read_text())
        self.run_spy.assert_not_called()

    def test_delete_takes_the_kept_original_with_it(self) -> None:
        self.req("DELETE", f"/studio/tracks/{self.DEL_ID}")
        self.assertEqual(studio_tracks.source_copies(self.DEL_ID), [])
        self.assertIsNone(mf.get(self.DEL_ID))

    def test_delete_with_scene_removes_the_block_and_rebuilds(self) -> None:
        code, raw = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}?scene=1")
        d = json.loads(raw)
        self.assertEqual(code, 200, d)
        self.assertTrue(d["ok"])
        self.assertTrue(d["scene_removed"])
        self.assertEqual(d["scenes"], ["vigil", "storm"])
        text = self.scenes.read_text()
        self.assertNotIn(self.DEL_ID, text)
        self.assertIn("- id: vigil", text)
        self.assertIn("- id: storm", text)
        self.assertEqual(self.scenes.with_suffix(".yaml.bak").read_text(),
                         self.ORIGINAL.format(tid=self.DEL_ID))
        ran = [Path(c[0][0][1]).name for c in self.run_spy.call_args_list]
        self.assertEqual(ran, ["render_audio.py", "gen_esphome.py",
                               "gen_previewer.py"])
        self.assertFalse(self.track.exists())

    def test_delete_with_scene_removes_an_orphan_whose_file_is_gone(self) -> None:
        """The file already gone, the scene still in scenes.yaml — the state
        a failed import or a hand-deleted file leaves. A plain DELETE is a
        404 (nothing to delete); ?scene=1 takes the orphan out (JB2-5a)."""
        self.track.unlink()
        code, _ = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}")
        self.assertEqual(code, 404)
        self.run_spy.assert_not_called()
        code, raw = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}?scene=1")
        d = json.loads(raw)
        self.assertEqual(code, 200, d)
        self.assertTrue(d["scene_removed"])
        self.assertTrue(d["file_missing"])
        self.assertEqual(d["scenes"], ["vigil", "storm"])
        self.assertNotIn(self.DEL_ID, self.scenes.read_text())
        self.assertEqual(studio_tracks.source_copies(self.DEL_ID), [])
        self.assertIsNone(mf.get(self.DEL_ID))
        self.assertTrue(self.run_spy.called)

    def test_delete_with_scene_of_a_track_not_in_the_show_is_harmless(self) -> None:
        self.scenes.write_text("scenes:\n  - id: vigil\n    duration_ms: 1\n")
        code, raw = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}?scene=1")
        d = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertFalse(d["scene_removed"])
        self.run_spy.assert_not_called()


class TestRouteBoundary(ServerCase):
    """/studio/* is the studio's, /api/* is the castle's — on every verb."""

    def test_unknown_studio_routes_are_404_and_unknown_api_routes_relay(self) -> None:
        # A typo'd studio route can no longer silently become a castle
        # call: outside /api/ the studio answers 404 for itself, and an
        # unknown /api/* path relays (castle_link.py) — with the fixture's
        # dead CASTLE_HOST that surfaces as the bridge's 502.
        for method, data in (("GET", None), ("POST", b"{}"), ("DELETE", None),
                             ("PUT", b"x")):
            for path in ("/nope", "/studio/nope", "/studio/tracks/../nope"):
                code, body = self.req(method, path, data)
                self.assertEqual(code, 404, (method, path))
                self.assertEqual(json.loads(body)["error"], "not found")
            code, body = self.req(method, "/api/nope", data)
            self.assertEqual(code, 502, method)
            self.assertIn("castle", json.loads(body)["error"])

    def test_old_api_spellings_still_answer_for_one_release(self) -> None:
        """A desk built before the move keeps working; the alias is logged
        once per route, not once per request."""
        studio._deprecated_seen.clear()
        with mock.patch("sys.stderr") as err:
            code, d = self.get_json("/api/tracks")
            self.assertEqual(code, 200)
            self.assertIn(self.WAVE_ID, [t["id"] for t in d["tracks"]])
            self.assertEqual(self.req("GET", f"/api/track/{self.WAVE_ID}")[0], 200)
            self.assertEqual(self.req("GET", "/api/waveform/_t_nope")[0], 404)
            self.assertEqual(self.req("GET", "/api/job/nope")[0], 404)
            self.get_json("/api/tracks")
        notes = [c.args[0] for c in err.write.call_args_list
                 if "DEPRECATED" in c.args[0]]
        self.assertEqual(len([n for n in notes if "/api/tracks" in n]), 1)
        self.assertTrue(all("/studio/" in n for n in notes))

    def test_the_castles_fire_a_scene_is_not_the_studios_editor(self) -> None:
        """/api/scene?s=<id> is the castle's; only the JSON-body spelling
        aliases to /studio/scene. Same old path, two different owners."""
        code, body = self.req("POST", "/api/scene?s=vigil", b"")
        self.assertEqual(code, 502)
        self.assertIn("castle", json.loads(body)["error"])
        code, d = self.post_json("/api/scene", {})
        self.assertEqual(code, 400)
        self.assertIn("need id and yaml", d["error"])

    def test_status_stays_at_api_status_and_marks_the_studio(self) -> None:
        """The desk's mode probe never moves: no castle in reach, the studio
        answers for itself and says so (device.ts reads the marker)."""
        code, d = self.get_json("/api/status")
        self.assertEqual((code, d), (200, {"studio": True}))
        self.assertEqual(self.req("GET", "/studio/status")[0], 404)

    def test_card_pull_relays_the_file_name_only(self) -> None:
        """/studio/card/<name> is the one relay that builds a castle path,
        so it is name-stripped like every other file route (grade report
        E1): "../api/status" must not reach any GET on the castle."""
        with mock.patch.object(studio.cl, "forward",
                               return_value=(200, b"x", "audio/mpeg")) as fw:
            self.assertEqual(self.req("GET", "/studio/card/song.mp3")[0], 200)
            self.assertEqual(fw.call_args[0][:2], ("GET", "/sd/song.mp3"))
            self.req("GET", "/studio/card/../api/status")
            self.assertEqual(fw.call_args[0][1], "/sd/status")
            self.req("GET", "/api/card/a/../b.mp3")          # the alias too
            self.assertEqual(fw.call_args[0][1], "/sd/b.mp3")
            code, body = self.req("GET", "/studio/card/")
            self.assertEqual(code, 400)
            self.assertIn("no file name", json.loads(body)["error"])
            self.assertEqual(fw.call_count, 3, "an empty name still relayed")


class TestRemoteRelay(ServerCase):
    def test_remote_is_handed_to_the_castle(self) -> None:
        page = b"<!doctype html><title>Castle Remote</title>"
        with mock.patch.object(studio.cl, "forward",
                               return_value=(200, page, "text/html; charset=utf-8")) as fw:
            code, body = self.req("GET", "/remote")
        self.assertEqual((code, body), (200, page))
        self.assertEqual(fw.call_args[0][:2], ("GET", "/remote"))

    def test_remote_without_a_castle_is_a_502_not_a_404(self) -> None:
        code, body = self.req("GET", "/remote")
        self.assertEqual(code, 502)
        self.assertIn("castle", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
