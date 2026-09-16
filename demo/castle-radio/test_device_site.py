"""The castle's one-file page: self-contained, shimmed first, honest text."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import device_site

HERE = Path(__file__).resolve().parent


def embedded(page: str, element_id: str):
    match = re.search(
        rf'id="{element_id}" type="application/json">(.*?)</script>', page
    )
    assert match, element_id
    return json.loads(match.group(1))


class FakeData(unittest.TestCase):
    """A .radio-data with one import whose audio exists and one whose does not."""

    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="castle-radio-data-"))
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        tracks = self.data / "tracks"
        tracks.mkdir()
        (tracks / "radio_aaaa.mp3").write_bytes(b"ID3" + b"\0" * 500)
        cues = [
            [0.07, "left", 1, 0.92],
            [0.081, "door", 0.65, 0.893],
            [0.3, "right", 1, 0.5],
        ]
        (self.data / "catalog.json").write_text(
            json.dumps(
                [
                    {
                        "key": "radio_aaaa",
                        "title": "Aaaa",
                        "duration": 6.5,
                        "cues": cues,
                        "split": True,
                    },
                    {"key": "radio_gone", "title": "Gone", "duration": 3, "cues": cues},
                ]
            )
        )


class TestBuild(FakeData):
    def test_page_is_one_self_contained_document(self):
        page = device_site.build(HERE, self.data).decode()
        self.assertNotIn('src="', page)
        self.assertNotIn("googleapis", page)
        self.assertNotIn('<link rel="stylesheet"', page)
        self.assertEqual(page.count("<style>"), 2)
        # the shim runs before app.js so its fetch override is in place
        self.assertLess(page.find("Castle direct:"), page.find("Standalone concept"))

    def test_media_streams_from_the_card_and_durations_come_from_scenes(self):
        page = device_site.build(HERE, self.data).decode()
        self.assertNotIn("media/${", page)
        self.assertIn("/sd/scenes/${tracks[id].file}", page)
        self.assertIn("audio.removeAttribute('src')", page)
        self.assertIn("/sd/scenes/${t.file}", page)
        self.assertNotIn("new Audio();probe", page)
        self.assertIn("window.castleDirect.scenes.find", page)

    def test_library_holds_only_playable_imports_with_reduced_frames(self):
        rows = embedded(device_site.build(HERE, self.data).decode(), "radio-library")
        self.assertEqual([r["key"] for r in rows], ["radio_aaaa"])
        row = rows[0]
        self.assertEqual(row["url"], "/sd/radio_aaaa.mp3")
        self.assertEqual(row["filename"], "radio_aaaa.mp3")
        self.assertEqual(row["bytes"], 503)
        self.assertFalse(row["split"])  # stems never reach the card
        self.assertFalse(row["source_available"])
        self.assertNotIn("cues", row)
        self.assertEqual(
            row["frames"], [[0.0, "towerL:a832ff@100"], [0.25, "towerR:4dff8c@100"]]
        )

    def test_scenes_are_inlined_without_their_yaml(self):
        scenes = embedded(device_site.build(HERE, self.data).decode(), "radio-scenes")
        self.assertEqual(len(scenes), 10)
        self.assertNotIn("yaml", scenes[0])
        self.assertEqual(scenes[0]["file"], "01_vigil.mp3")

    def test_the_computer_is_named_only_where_it_is_true(self):
        page = device_site.build(HERE, self.data).decode()
        for gone in (
            "Castle unreachable at 10.27.27.81",
            "Sending command to 10.27.27.81",
            "Control room server is not running",
            "Start server.py",
            "files stay in this demo",
            "Waveform unavailable. Reopen",
        ):
            self.assertNotIn(gone, page)
        self.assertIn("This browser", page)
        self.assertIn('<audio id="audio" preload="none">', page)

    def test_no_catalog_means_an_empty_library(self):
        rows = embedded(
            device_site.build(HERE, self.data / "nowhere").decode(), "radio-library"
        )
        self.assertEqual(rows, [])

    def test_a_moved_string_fails_the_build_not_the_porch(self):
        with self.assertRaises(SystemExit) as cm:
            device_site.rewritten("app.js", "nothing like the source")
        self.assertIn("expected exactly one", str(cm.exception))

    def test_json_cannot_close_the_script_element(self):
        self.assertNotIn(
            "</", device_site.inline_json({"x": "</script><b>"}).replace("<\\/", "")
        )

    def test_inventory_and_version_parse_match_the_python_bridge(self):
        page = (HERE / "castle-direct.js").read_text()
        self.assertIn("f.name && !f.dir", page)
        self.assertIn("/^(\\d+)\\.(\\d+)/.exec(", page)
        self.assertIn("hex.toLowerCase()", page)


if __name__ == "__main__":
    unittest.main()
