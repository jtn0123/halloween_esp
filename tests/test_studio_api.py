"""Tests for the studio server's HTTP surface.

Drives a real server on an ephemeral port. These are the endpoints the Tracks
panel depends on, so a break here is a browser UI that silently does nothing.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import manifest as mf
import studio
import studio_http as sh
from helpers import make_click_track
from studio_case import ServerCase, make_mp3


class TestReads(ServerCase):
    """Everything the page asks for before the user has done anything."""

    def test_root_serves_the_previewer(self) -> None:
        code, body = self.req("GET", "/")
        if code == 404:
            # An unbuilt checkout is a legitimate state; it must still be a
            # clean JSON 404 rather than a traceback.
            self.assertIn("previewer not built", json.loads(body)["error"])
            return
        self.assertEqual(code, 200)
        self.assertIn(b"<", body[:200])
        self.assertGreater(len(body), 1000)

    def test_index_html_is_the_same_page(self) -> None:
        self.assertEqual(self.req("GET", "/")[0], self.req("GET", "/index.html")[0])

    def test_tracks_lists_files_and_scene_ids(self) -> None:
        code, d = self.get_json("/studio/tracks")
        self.assertEqual(code, 200)
        self.assertEqual(set(d), {"tracks", "scenes"})
        ids = [t["id"] for t in d["tracks"]]
        self.assertIn(self.WAVE_ID, ids)
        self.assertEqual(d["scenes"], studio.scene_ids())
        self.assertIn("vigil", d["scenes"])

    def test_track_entries_carry_duration_and_onset_counts(self) -> None:
        """The panel shows both; a track missing them looks broken to the user."""
        _, d = self.get_json("/studio/tracks")
        mine = next(t for t in d["tracks"] if t["id"] == self.WAVE_ID)
        self.assertAlmostEqual(mine["dur"], 3.0, delta=0.3)
        self.assertGreater(mine["kb"], 0)
        self.assertIn("onset_low", mine["onsets"])

    def test_waveform_returns_a_drawable_envelope(self) -> None:
        code, d = self.get_json(f"/studio/waveform/{self.WAVE_ID}")
        self.assertEqual(code, 200)
        self.assertEqual(d["id"], self.WAVE_ID)
        self.assertAlmostEqual(d["duration"], 3.0, delta=0.3)
        self.assertGreater(len(d["peaks"]), 100)
        self.assertLessEqual(max(d["peaks"]), 1.0)
        self.assertIn("onset_low", d["onsets"])

    def test_waveform_honours_the_sensitivity_query(self) -> None:
        """The clip editor's slider is only useful if it changes the answer."""
        _, loose = self.get_json(f"/studio/waveform/{self.WAVE_ID}?sensitivity=0.6")
        _, tight = self.get_json(f"/studio/waveform/{self.WAVE_ID}?sensitivity=2.5")
        self.assertGreaterEqual(
            len(loose["onsets"]["onset_low"]), len(tight["onsets"]["onset_low"])
        )

    def test_waveform_of_an_unknown_track_is_404(self) -> None:
        code, d = self.get_json("/studio/waveform/_t_studio_missing")
        self.assertEqual(code, 404)
        self.assertIn("no such track", d["error"])

    def test_track_streams_the_actual_bytes(self) -> None:
        code, body = self.req("GET", f"/studio/track/{self.WAVE_ID}.mp3")
        self.assertEqual(code, 200)
        self.assertEqual(body, self.wave.read_bytes())

    def test_track_of_an_unknown_id_is_404(self) -> None:
        self.assertEqual(self.req("GET", "/studio/track/_t_studio_missing.mp3")[0], 404)

    def test_track_refuses_a_non_audio_file(self) -> None:
        """tracks/ holds other things — tracks.json, a README — and none of
        them should be readable through the audio route."""
        self.assertEqual(self.req("GET", "/studio/track/tracks.json")[0], 404)
        self.assertEqual(self.req("GET", "/studio/track/README.md")[0], 404)

    def test_track_streams_without_being_told_the_extension(self) -> None:
        """The panel knows the id; only the server knows the container. If it
        had to guess ".mp3" — which it used to — every non-MP3 import is a row
        whose Play button does nothing."""
        code, body = self.req("GET", f"/studio/track/{self.WAVE_ID}")
        self.assertEqual(code, 200)
        self.assertEqual(body, self.wave.read_bytes())

    def test_a_wav_track_is_listed_playable_and_typed(self) -> None:
        _, d = self.get_json("/studio/tracks")
        mine = next((t for t in d["tracks"] if t["id"] == self.WAV_ID), None)
        self.assertIsNotNone(mine, "a WAV import never appeared in the list")
        self.assertEqual(mine["ext"], "wav")  # type: ignore[index]  # narrowed by assertIsNotNone
        code, body = self.req("GET", f"/studio/track/{self.WAV_ID}")
        self.assertEqual(code, 200)
        self.assertEqual(body, self.wav.read_bytes())

    def test_a_wav_track_has_a_drawable_waveform(self) -> None:
        """The clip editor is reached by the same id, so it has to resolve the
        container too."""
        code, d = self.get_json(f"/studio/waveform/{self.WAV_ID}")
        self.assertEqual(code, 200)
        self.assertGreater(len(d["peaks"]), 100)

    def test_track_serves_a_byte_range(self) -> None:
        """Seeking into a four-minute import without this means downloading the
        whole thing first."""
        total = self.wave.stat().st_size
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/studio/track/{self.WAVE_ID}",
            headers={"Range": "bytes=10-19"},
        )
        with urllib.request.urlopen(r, timeout=20) as f:
            self.assertEqual(f.status, 206)
            self.assertEqual(f.headers["Content-Range"], f"bytes 10-19/{total}")
            self.assertEqual(f.read(), self.wave.read_bytes()[10:20])

    def test_a_range_the_file_cannot_satisfy_falls_back_to_all_of_it(self) -> None:
        """A media element occasionally asks for more than there is. Answering
        with the whole file is survivable; answering with a truncated 206 that
        claims to be a range is not."""
        for bad in ("bytes=99999999-", "bytes=abc-def", "bytes=5-1", "nonsense"):
            r = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/studio/track/{self.WAVE_ID}",
                headers={"Range": bad},
            )
            with urllib.request.urlopen(r, timeout=20) as f:
                self.assertEqual(f.status, 200, bad)
                self.assertEqual(f.read(), self.wave.read_bytes(), bad)


class TestJobs(ServerCase):
    """The async import surface, without ever starting a real import."""

    def test_unknown_job_is_404(self) -> None:
        code, d = self.get_json("/studio/job/deadbeefdead")
        self.assertEqual(code, 404)
        self.assertIn("no such job", d["error"])

    def test_a_finished_job_carries_the_refreshed_track_list(self) -> None:
        """The page stops polling on `done`, so the fresh list has to ride
        along with the last response or the new track never appears."""
        job = studio._runner.start(["true"])
        for _ in range(400):
            code, d = self.get_json(f"/studio/job/{job.id}")
            self.assertEqual(code, 200)
            if d["done"]:
                break
            threading.Event().wait(0.01)
        self.assertTrue(d["done"])
        self.assertEqual(d["phase"], "done")
        self.assertIn(self.WAVE_ID, [t["id"] for t in d["tracks"]])

    def test_async_import_rejects_a_non_http_url(self) -> None:
        code, d = self.post_json("/studio/import/async", {"url": "file:///etc/passwd"})
        self.assertEqual(code, 400)
        self.assertIn("http(s)", d["error"])

    def test_async_import_rejects_an_empty_body(self) -> None:
        code, _ = self.req(
            "POST", "/studio/import/async", b"", {"Content-Type": "application/json"}
        )
        self.assertEqual(code, 400)

    def test_async_import_builds_the_import_command(self) -> None:
        """The options the panel sends have to survive the trip to argv —
        this is where a renamed field silently stops taking effect."""
        with mock.patch.object(
            studio._runner, "start", return_value=studio.sj.Job(id="fakejobid123")
        ) as spy:
            code, d = self.post_json(
                "/studio/import/async",
                {
                    "url": "https://example.invalid/v",
                    "id": "_t_studio_fake",
                    "start": "0:12",
                    "take": 24,
                    "sample_rate": 22050,
                    "gain_db": "",
                    "normalize": True,
                },
            )
        self.assertEqual(code, 200)
        self.assertEqual(d["id"], "fakejobid123")
        argv = spy.call_args[0][0]
        self.assertEqual(argv[0], studio.PY)
        self.assertTrue(argv[1].endswith("import_track.py"))
        self.assertEqual(argv[2], "https://example.invalid/v")
        self.assertTrue(any(a.startswith("--start=") for a in argv))
        self.assertTrue(
            any(a.startswith("--sample-rate=") for a in argv)
        )  # underscores become dashes
        self.assertIn("--normalize", argv)
        self.assertNotIn("--gain-db", argv, "an empty option was passed through")


class TestWrites(ServerCase):
    """Requests that change something. Subprocess work is stubbed out."""

    def test_delete_removes_the_file_and_forgets_the_source(self) -> None:
        target = studio.TRACKS / f"{self.DEL_ID}.mp3"
        make_mp3(target, seconds=1.0)
        mf.record(self.DEL_ID, source="file:/tmp/whatever", title="test")
        self.assertIsNotNone(mf.get(self.DEL_ID))

        code, d = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(d)["removed"], self.DEL_ID)
        self.assertFalse(target.exists(), "the file is still on disk")
        self.assertIsNone(mf.get(self.DEL_ID), "the manifest still remembers it")

    def test_delete_of_an_unknown_track_is_404(self) -> None:
        code, body = self.req("DELETE", "/studio/tracks/_t_studio_never_existed")
        self.assertEqual(code, 404)
        self.assertEqual(json.loads(body)["error"], "not found")

    def test_delete_removes_a_non_mp3_track_too(self) -> None:
        """Otherwise Delete reports success on a file that is still there, and
        the row comes back on the next refresh."""
        target = studio.TRACKS / f"{self.DEL_ID}_flac.wav"
        make_click_track(target, seconds=1.0)
        code, _ = self.req("DELETE", f"/studio/tracks/{self.DEL_ID}_flac")
        self.assertEqual(code, 200)
        self.assertFalse(target.exists(), "the WAV is still on disk")

    def test_probe_of_a_non_url_answers_without_touching_the_network(self) -> None:
        """The guard has to come before yt-dlp, not after: a typo in the box
        should cost nothing and, in particular, must not shell out."""
        with mock.patch(
            "studio_media.subprocess.run",
            side_effect=AssertionError("probe shelled out"),
        ):
            code, d = self.post_json("/studio/probe", {"url": "not a link at all"})
        # 400, not 200: a bad link is the caller's mistake, and the status
        # code is allowed to say so (grade report B2).
        self.assertEqual(code, 400)
        self.assertFalse(d["ok"])
        self.assertIn("link", d["error"])

    def test_probe_of_an_empty_body_is_not_a_crash(self) -> None:
        with mock.patch(
            "studio_media.subprocess.run",
            side_effect=AssertionError("probe shelled out"),
        ):
            code, d = self.req(
                "POST", "/studio/probe", b"", {"Content-Type": "application/json"}
            )
        self.assertEqual(code, 400)  # no url = the caller's mistake (B2)
        self.assertFalse(json.loads(d)["ok"])

    def test_sync_import_rejects_a_missing_url(self) -> None:
        code, d = self.post_json("/studio/import", {})
        self.assertEqual(code, 400)
        self.assertEqual(d["error"], "no url")

    def test_sync_import_rejects_a_non_http_url(self) -> None:
        code, d = self.post_json("/studio/import", {"url": "ftp://example.invalid/x"})
        self.assertEqual(code, 400)
        self.assertIn("http(s)", d["error"])

    def test_upload_writes_the_file_and_hands_it_to_the_importer(self) -> None:
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            # Read the staged upload while it still exists — proving the file
            # reached disk before the importer was invoked.
            calls.append(Path(cmd[2]).read_bytes())
            return True, "pretend log"

        body = (
            b'--B\r\nContent-Disposition: form-data; name="file"; '
            b'filename="clip.wav"\r\n\r\nRIFFfake\r\n--B--\r\n'
        )
        with mock.patch.object(studio, "run", fake_run):
            code, d = self.req(
                "POST",
                "/studio/import",
                body,
                {
                    "Content-Type": "multipart/form-data; boundary=B",
                    "X-Import-Opts": json.dumps({"id": "_t_studio_up", "take": 5}),
                },
            )
        self.assertEqual(code, 200)
        d = json.loads(d)
        self.assertTrue(d["ok"])
        self.assertEqual(d["log"], "pretend log")
        self.assertEqual(calls[1], b"RIFFfake")
        self.assertTrue(any(a.startswith("--take=") for a in calls[0]))
        self.assertTrue(calls[0][2].endswith("clip.wav"))
        self.assertFalse(
            (studio.TRACKS / "_upload").exists(),
            "the staging directory was left behind",
        )

    def test_upload_without_a_file_is_400(self) -> None:
        code, d = self.req(
            "POST",
            "/studio/import",
            b"--B--\r\n",
            {"Content-Type": "multipart/form-data; boundary=B"},
        )
        self.assertEqual(code, 400)
        self.assertIn("no file", json.loads(d)["error"])

    def test_malformed_upload_options_are_a_400_not_a_traceback(self) -> None:
        """X-Import-Opts goes through the same JSON boundary as a body."""
        body = (
            b'--B\r\nContent-Disposition: form-data; name="file"; '
            b'filename="clip.wav"\r\n\r\nRIFFfake\r\n--B--\r\n'
        )
        with mock.patch.object(studio, "run") as run:
            code, d = self.req(
                "POST",
                "/studio/import",
                body,
                {
                    "Content-Type": "multipart/form-data; boundary=B",
                    "X-Import-Opts": "{not json",
                },
            )
        self.assertEqual(code, 400)
        self.assertIn("not valid JSON", json.loads(d)["error"])
        run.assert_not_called()
        self.assertFalse((studio.TRACKS / "_upload").exists())

    def test_a_body_larger_than_the_cap_is_refused_up_front(self) -> None:
        """The server buffers bodies whole; a Content-Length it would never
        allocate for is a 400 before a byte is read."""
        code, d = self.req(
            "POST",
            "/studio/probe",
            b"{}",
            {
                "Content-Type": "application/json",
                "Content-Length": str(sh.MAX_BODY + 1),
            },
        )
        self.assertEqual(code, 400)
        self.assertIn("too large", json.loads(d)["error"])

    def test_refresh_needs_an_id(self) -> None:
        code, d = self.post_json("/studio/refresh", {})
        self.assertEqual(code, 400)
        self.assertEqual(d["error"], "no id")

    def test_refresh_reimports_by_id_with_overrides(self) -> None:
        """--refresh is the reason the manifest exists; the id and the changed
        option both have to reach import_track."""
        with mock.patch.object(studio, "run", return_value=(True, "log")) as spy:
            code, d = self.post_json(
                "/studio/refresh",
                {"id": self.WAVE_ID, "take": 12, "start": "", "normalize": True},
            )
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"])
        argv = spy.call_args[0][0]
        self.assertIn("--refresh", argv)
        self.assertIn(self.WAVE_ID, argv)
        self.assertTrue(any(a.startswith("--take=") for a in argv))
        self.assertIn("--normalize", argv)
        self.assertFalse(any(a.startswith("--start=") for a in argv))
        self.assertIn(self.WAVE_ID, [t["id"] for t in d["tracks"]])

    def test_rebuild_runs_the_three_generators(self) -> None:
        with mock.patch.object(studio, "run", return_value=(True, "out")) as spy:
            code, d = self.post_json("/studio/rebuild", {})
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"])
        ran = [Path(c[0][0][1]).name for c in spy.call_args_list]
        self.assertEqual(ran, ["render_audio.py", "gen_esphome.py", "gen_previewer.py"])

    def test_rebuild_reports_failure_of_any_step(self) -> None:
        with mock.patch.object(
            studio, "run", side_effect=[(True, "a"), (False, "b"), (True, "c")]
        ):
            _, d = self.post_json("/studio/rebuild", {})
        self.assertFalse(d["ok"])
        self.assertIn("b", d["log"])
