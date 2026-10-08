"""The publish handshake (tools/fw_formats.py): a card format the castle's
firmware cannot read is refused before a byte is sent, by every path that
sends one — sd_sync (so `make publish` and the studio's publish) and Castle
Radio's sync (demo/castle-radio/test_format_handshake.py walks that one)."""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)

# isort: split
import build_paths as bp
import cue_file
import fw_formats
import scene_manifest
import sd_sync
from test_sd_sync import FakeCard


def header(magic: bytes, version: int) -> bytes:
    return cue_file.HEADER.pack(magic, version, 0, 0, 0, 0)


CUE_1 = header(cue_file.MAGIC, cue_file.VERSION)
CUE_2 = header(cue_file.MAGIC, cue_file.VERSION_2)
MAN_1 = header(scene_manifest.MAGIC, scene_manifest.VERSION)


def k_version(name: str) -> int:
    text = (ROOT / "firmware" / name).read_text(encoding="utf-8")
    m = re.search(r"constexpr uint8_t kVersion = (\d+);", text)
    assert m, f"no kVersion in {name}"
    return int(m[1])


class Table(unittest.TestCase):
    """READERS is the one table, and it may not drift from either side."""

    def test_every_version_a_writer_makes_has_a_row(self) -> None:
        self.assertEqual(set(fw_formats.READERS["cue"]), set(cue_file.READ_VERSIONS))
        self.assertIn(scene_manifest.VERSION, fw_formats.READERS["show.man"])

    def test_the_newest_row_is_what_the_firmware_headers_read(self) -> None:
        self.assertEqual(max(fw_formats.READERS["cue"]), k_version("castle_cues.h"))
        self.assertEqual(
            max(fw_formats.READERS["show.man"]), k_version("castle_scenes.h")
        )

    def test_the_build_in_this_tree_reads_everything_it_lists(self) -> None:
        here = fw_formats.parse_version(fw_formats.this_firmware())
        assert here is not None
        for kind, rows in fw_formats.READERS.items():
            for version, first in rows.items():
                self.assertLessEqual(first, here, f"{kind} v{version}")

    def test_versions_parse_as_the_castle_spells_them(self) -> None:
        self.assertEqual(fw_formats.parse_version("5.75"), (5, 75))
        self.assertEqual(fw_formats.parse_version("v5.9-dev"), (5, 9))
        for bad in ("", "five", None, 5.75):
            self.assertIsNone(fw_formats.parse_version(bad))

    def test_the_studio_finds_the_refusal_by_the_same_words(self) -> None:
        rust = (ROOT / "core" / "src" / "studio_publish.rs").read_text(encoding="utf-8")
        m = re.search(r'pub const UPDATE_FIRST: &str = "([^"]+)";', rust)
        self.assertIsNotNone(m)
        assert m
        self.assertEqual(m[1], fw_formats.UPDATE_FIRST)


class Refusal(unittest.TestCase):
    def test_an_old_castle_is_told_to_update_first(self) -> None:
        why = fw_formats.refusal({"version": "5.70"}, {"storm.cue": CUE_2})
        assert why
        self.assertIn("storm.cue", why)
        self.assertIn("5.71 or newer", why)
        self.assertIn("runs 5.70", why)
        self.assertIn(fw_formats.UPDATE_FIRST, why)

    def test_a_castle_that_reads_the_format_is_sent_it(self) -> None:
        files = {"storm.cue": CUE_2, "show.man": MAN_1, "a.mp3": b"ID3"}
        for version in ("5.71", "5.75", "6.0"):
            self.assertIsNone(fw_formats.refusal({"version": version}, files))
        self.assertIsNone(fw_formats.refusal({"version": "5.63"}, {"a.cue": CUE_1}))

    def test_the_newest_need_is_the_one_named(self) -> None:
        files = {"a.cue": CUE_1, "show.man": MAN_1, "b.cue": CUE_2}
        why = fw_formats.refusal({"version": "5.62"}, files)
        assert why
        self.assertIn("b.cue", why)
        self.assertIn("5.71", why)
        why = fw_formats.refusal({"version": "5.66"}, {"show.man": MAN_1})
        assert why
        self.assertIn("scene list format 1", why)

    def test_a_castle_that_names_no_version_is_refused(self) -> None:
        for status in ({}, {"version": ""}, {"version": "dev"}):
            why = fw_formats.refusal(status, {"a.cue": CUE_1})
            assert why
            self.assertIn("does not say", why)

    def test_a_format_no_firmware_reads_is_refused_on_any_castle(self) -> None:
        for head in (header(cue_file.MAGIC, 9), b"RIFF0000", b""):
            why = fw_formats.refusal({"version": "9.99"}, {"x.cue": head})
            assert why
            self.assertIn("no castle firmware reads", why)

    def test_files_it_does_not_govern_cost_nothing(self) -> None:
        self.assertIsNone(fw_formats.refusal({}, {"a.mp3": b"", "x.show.json": b"{"}))


class Publish(unittest.TestCase):
    """sd_sync's two format pushes stop before their first PUT."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-fwf-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        audio = self.tmp / "audio"
        (audio / "card" / "scenes").mkdir(parents=True)
        (audio / "card" / "cues").mkdir(parents=True)
        (audio / "card" / "01_storm.mp3").write_bytes(b"\xff\xfb" * 64)
        (audio / "card" / "scenes" / "storm.cue").write_bytes(CUE_2 + b"\0" * 8)
        (audio / "card" / "scenes" / "show.man").write_bytes(MAN_1)
        (audio / "card" / "cues" / "song.cue").write_bytes(CUE_2)
        patch = mock.patch.object(bp, "AUDIO", audio)
        patch.start()
        self.addCleanup(patch.stop)

    def card(self, version: str) -> FakeCard:
        card = FakeCard({"song.mp3": 10})
        real = card.__call__

        def answer(ip: str, method: str, path: str, *a: object, **k: object) -> bytes:
            if method == "GET" and path == "/api/status":
                card.calls.append((method, path, 0))
                return f'{{"version": "{version}"}}'.encode()
            return real(ip, method, path, *a, **k)  # type: ignore[arg-type]

        patch = mock.patch.object(sd_sync, "api", answer)
        patch.start()
        self.addCleanup(patch.stop)
        return card

    def test_scenes_and_cues_refuse_an_old_castle_before_sending(self) -> None:
        for cmd in (sd_sync.cmd_scenes, sd_sync.cmd_cues):
            card = self.card("5.70")
            with self.assertRaises(SystemExit) as caught:
                cmd("castle")
            self.assertIn(fw_formats.UPDATE_FIRST, str(caught.exception))
            self.assertFalse([c for c in card.calls if c[0] == "PUT"], cmd.__name__)

    def test_a_castle_new_enough_gets_the_show(self) -> None:
        card = self.card("5.75")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sd_sync.cmd_cues("castle"), 0)
        self.assertIn(("PUT", "/api/files/song.cue", len(CUE_2)), card.calls)

    def test_no_format_file_means_no_question(self) -> None:
        calls: list[str] = []

        def api(ip: str, method: str, path: str, *a: object, **k: object) -> bytes:
            calls.append(path)
            raise OSError("not asked")

        fw_formats.check_publish("castle", api, [self.tmp / "audio" / "x.mp3"])
        self.assertEqual(calls, [])
        with self.assertRaises(SystemExit) as caught:
            fw_formats.check_publish(
                "castle", api, [self.tmp / "audio" / "card" / "cues" / "song.cue"]
            )
        self.assertIn("did not say which firmware", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
