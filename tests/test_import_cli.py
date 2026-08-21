"""Tests for the importer's command line.

`test_import.py` covers the pieces; this drives `main()` the way a person or
the studio server actually does. That path is most of the module and none of
it was exercised — including `--refresh`, which is the whole reason the
provenance manifest exists.

Nothing here touches the network. The URL branch is deliberately not driven:
it depends on someone else's servers, and a test that fails when YouTube
changes its markup teaches nothing about this code.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import import_track as it
import manifest as mf
from helpers import make_click_track


class CliCase(unittest.TestCase):
    """Redirects tracks/ and the manifest into a tempdir.

    The real tracks/ holds the user's own audio. A test that wrote there —
    or worse, deleted from it — would be destroying data to check a
    behaviour, which is never a trade worth making.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src.wav"
        make_click_track(self.src, seconds=4.0)

        self._tracks, self._mfpath = it.TRACKS, mf.PATH
        it.TRACKS = self.tmp / "tracks"
        it.TRACKS.mkdir()
        mf.PATH = it.TRACKS / "tracks.json"

    def tearDown(self) -> None:
        it.TRACKS, mf.PATH = self._tracks, self._mfpath
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args: str) -> tuple[int, str]:
        argv = sys.argv
        out = io.StringIO()
        try:
            sys.argv = ["import_track.py", *args]
            with contextlib.redirect_stdout(out):
                code = it.main()
        finally:
            sys.argv = argv
        return code, out.getvalue()


class TestImportFromFile(CliCase):
    def test_imports_and_reports(self) -> None:
        code, out = self.run_cli(str(self.src), "--id", "demo")
        self.assertEqual(code, 0)
        self.assertTrue((it.TRACKS / "demo.mp3").exists())
        self.assertIn("imported", out)
        self.assertIn("demo.mp3", out)

    def test_prints_a_pasteable_scene(self) -> None:
        """The output is the handover to scenes.yaml; if it stops being valid
        YAML the workflow silently breaks at the paste step."""
        import yaml
        _code, out = self.run_cli(str(self.src), "--id", "demo")
        block = out[out.index("  - id: demo"):out.index("then:")]
        doc = yaml.safe_load("scenes:\n" + block)
        self.assertEqual(doc["scenes"][0]["id"], "demo")

    def test_records_provenance(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--notes", "a note")
        entry = mf.get("demo")
        assert entry is not None
        self.assertTrue(entry["source"].startswith("file:"))
        self.assertEqual(entry["notes"], "a note")
        # Stereo is the default: the show runs two speaker chains.
        self.assertEqual(entry["audio"]["channels"], 2)

    def test_id_is_derived_from_the_filename_when_omitted(self) -> None:
        self.run_cli(str(self.src))
        self.assertTrue((it.TRACKS / "src.mp3").exists())

    def test_reports_the_flash_cost(self) -> None:
        """Every import competes for the same budget, so the cost belongs in
        front of you at the moment you make the choice."""
        _code, out = self.run_cli(str(self.src), "--id", "demo")
        self.assertIn("budget", out)

    def test_missing_file_fails_loudly(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_cli(str(self.tmp / "nope.wav"), "--id", "x")

    def test_no_source_fails(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_cli("--id", "x")

    def test_a_failed_convert_leaves_no_track_behind(self) -> None:
        """Pass-1 J1-2: ffmpeg opens its output before it knows the input is
        junk, so a failed import used to leave a 0-byte track the desk then
        offered to send to the castle."""
        junk = self.tmp / "junk.mp3"
        junk.write_bytes(b"this is not audio" * 100)
        with self.assertRaises(SystemExit) as cm:
            self.run_cli(str(junk), "--id", "junk")
        self.assertIn("playable audio", str(cm.exception))
        self.assertEqual(sorted(p.name for p in it.TRACKS.iterdir()), [])
        self.assertIsNone(mf.get("junk"))

    def test_a_failed_refresh_keeps_the_good_copy(self) -> None:
        self.run_cli(str(self.src), "--id", "keep")
        out = it.TRACKS / "keep.mp3"
        before = out.read_bytes()
        # The remembered source turns to junk underneath the re-import.
        self.src.write_bytes(b"not audio any more")
        with self.assertRaises(SystemExit):
            self.run_cli("--refresh", "keep")
        self.assertEqual(out.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in it.TRACKS.iterdir()
                                if p.name.endswith(".part")), [])


class TestOptions(CliCase):
    def test_take_and_bitrate_are_applied_and_remembered(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--take", "2",
                     "--bitrate", "64")
        entry = mf.get("demo")
        assert entry is not None
        self.assertEqual(entry["opts"]["bitrate"], 64)
        self.assertAlmostEqual(entry["audio"]["duration"], 2.0, delta=0.3)

    def test_sample_rate_and_channels(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--sample-rate", "22050",
                     "--channels", "2")
        entry = mf.get("demo")
        assert entry is not None
        self.assertEqual(entry["audio"]["sample_rate"], 22050)
        self.assertEqual(entry["audio"]["channels"], 2)

    def test_normalize_is_recorded(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--normalize")
        entry = mf.get("demo")
        assert entry is not None
        self.assertTrue(entry["opts"]["normalize"])


class TestRefresh(CliCase):
    """`--refresh` is the reason the manifest exists: rebuild a track from its
    remembered source with one setting changed, without going to find the
    link again."""

    def test_rebuilds_from_the_remembered_source(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--bitrate", "64")
        code, out = self.run_cli("--refresh", "demo", "--bitrate", "96")
        self.assertEqual(code, 0)
        self.assertIn("96kbps", out)
        entry = mf.get("demo")
        assert entry is not None
        self.assertEqual(entry["opts"]["bitrate"], 96)

    def test_unchanged_options_are_carried_forward(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--take", "2",
                     "--normalize", "--sample-rate", "22050")
        self.run_cli("--refresh", "demo", "--bitrate", "96")
        entry = mf.get("demo")
        assert entry is not None
        self.assertTrue(entry["opts"]["normalize"], "normalize was forgotten")
        self.assertEqual(entry["opts"]["sample_rate"], 22050)

    def test_notes_survive_a_refresh(self) -> None:
        self.run_cli(str(self.src), "--id", "demo", "--notes", "keep me")
        self.run_cli("--refresh", "demo")
        entry = mf.get("demo")
        assert entry is not None
        self.assertEqual(entry["notes"], "keep me")

    def test_unknown_id_fails_with_a_useful_message(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("--refresh", "never_imported")
        self.assertIn("--list", str(cm.exception))


class TestListAndAnalyze(CliCase):
    def test_list_when_empty(self) -> None:
        code, out = self.run_cli("--list")
        self.assertEqual(code, 0)
        self.assertIn("no tracks", out)

    def test_list_shows_imported_tracks(self) -> None:
        self.run_cli(str(self.src), "--id", "demo")
        _code, out = self.run_cli("--list")
        self.assertIn("demo", out)

    def test_analyze_only_does_not_import(self) -> None:
        """Looking before committing — it must not leave a file behind."""
        code, out = self.run_cli(str(self.src), "--analyze-only")
        self.assertEqual(code, 0)
        self.assertIn("onset", out)
        self.assertEqual(list(it.TRACKS.glob("*.mp3")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
