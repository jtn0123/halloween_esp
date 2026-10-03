"""tools/guide_shots.py + guide_shots_radio.py — the owner's guide's pictures.

The browser half (web/guide/shots.ts) needs Chromium and is run by `make
guide-shots`, not here. What runs here is everything around it: the
pictures in docs/guide/ are exactly the ones the guide shows and SHOTS
names, under budget; the stand-in servers serve what the browser will
photograph; and the pretend LAN Castle Radio runs on cannot reach anything
but loopback — a picture is never a reason to touch a real castle.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_find
import guide_shots as gs
import guide_shots_radio as gsr
import helpers  # noqa: F401  (the sandbox scrub)
import unit_label


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        answer: dict[str, Any] = json.loads(r.read())
    return answer


class TestTheGuidesPictures(unittest.TestCase):
    def test_the_folder_is_the_guides_and_shots_and_under_budget(self) -> None:
        self.assertEqual(gs.audit(), [])

    def test_every_picture_is_a_small_palette_png(self) -> None:
        for stem in gs.SHOTS:
            with Image.open(gs.OUT / f"{stem}.png") as im:
                self.assertEqual((im.format, im.mode), ("PNG", "P"), stem)
                self.assertFalse(
                    im.info.keys() & {"Comment", "Description", "Author"}, stem
                )

    def test_the_guide_has_no_raw_todo_left(self) -> None:
        text = gs.GUIDE.read_text(encoding="utf-8")
        outside = "".join(part.split("-->", 1)[-1] for part in text.split("<!--"))
        self.assertNotIn("TODO", outside)
        self.assertNotIn("next release", text)

    def test_audit_names_each_kind_of_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder, guide = Path(tmp) / "guide", Path(tmp) / "G.md"
            folder.mkdir()
            guide.write_text(
                '![a](guide/portal.png) <img src="guide/ghost.png">', encoding="utf-8"
            )
            Image.new("RGB", (4, 4)).save(folder / "portal.png")
            Image.new("RGB", (4, 4)).save(folder / "stray.png")
            with mock.patch.object(gs, "BUDGET_KB", 0):
                problems = gs.audit(guide, folder)
        self.assertIn(
            "the guide shows guide/ghost.png, which is not in " + str(folder), problems
        )
        self.assertIn(f"{folder}/stray.png is not shown by the guide", problems)
        self.assertIn("flasher is in SHOTS but was never made", problems)
        self.assertIn(f"{folder}/stray.png is not in SHOTS", problems)
        self.assertTrue(problems[-1].endswith("over its 0 KB budget"))

    def test_compress_keeps_the_size_and_drops_to_a_palette(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw, out = Path(tmp) / "raw.png", Path(tmp) / "x" / "out.png"
            im = Image.new("RGB", (300, 200))
            im.putdata(
                [
                    ((x * 7) % 256, (y * 3) % 256, 90)
                    for y in range(200)
                    for x in range(300)
                ]
            )
            im.save(raw)
            size = gs.compress(raw, out, colours=16)
            self.assertEqual(size, out.stat().st_size)
            with Image.open(out) as small:
                self.assertEqual((small.size, small.mode), ((300, 200), "P"))
                self.assertLessEqual(len(small.getcolors() or []), 16)


class TestStandIns(unittest.TestCase):
    def test_the_portal_page_is_esphomes_own(self) -> None:
        html = gs.portal_page().decode()
        for want in ("/config.json", "WiFi Networks", "/wifisave", "name=psk"):
            self.assertIn(want, html)

    def test_a_portal_header_without_the_array_is_said(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            header = Path(tmp) / "components" / "captive_portal" / "captive_index.h"
            header.parent.mkdir(parents=True)
            header.write_text("// nothing here\n", encoding="utf-8")
            with self.assertRaisesRegex(gs.ShotError, "no longer holds INDEX_GZ"):
                gs.portal_page(Path(tmp))

    def test_the_portal_names_the_castle_the_label_names(self) -> None:
        config = gs.portal_config()
        self.assertEqual(config["name"], unit_label.from_mac(gs.MAC).name)
        self.assertEqual(config["name"], gsr.CASTLE_NAME)
        self.assertEqual(str(config["mac"]).replace(":", "").lower(), gs.MAC.hex())
        aps = config["aps"]
        assert isinstance(aps, list)
        self.assertEqual(aps[0], {})  # the page skips the castle's own
        self.assertEqual(len(aps), len(gs.NETWORKS) + 1)

    def test_the_site_serves_the_portal_and_the_flasher(self) -> None:
        with gs.site() as base:
            self.assertIn(b"WiFi Networks", get(f"{base}/")[1])
            self.assertEqual(
                json.loads(get(f"{base}/config.json")[1])["name"], "castle-a1b2c3"
            )
            self.assertIn(b"Connect to the castle", get(f"{base}/flasher/")[1])
            manifest = json.loads(get(f"{base}/flasher/flasher-manifest.json")[1])
            self.assertEqual(manifest["version"], f"v{gs.app_version()}")
            self.assertEqual(get(f"{base}/elsewhere")[0], 404)

    def test_seeded_songs_take_their_size_and_no_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gs.seed_card(Path(tmp) / "card", {"a.mp3": 5_000_000})
            song = Path(tmp) / "card" / "a.mp3"
            self.assertEqual(song.stat().st_size, 5_000_000)

    def test_the_three_castles_are_buyer_castles_in_three_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, gs.castles(Path(tmp)) as emus:
            status = {k: json.loads(get(f"http://127.0.0.1:{e.port}/api/status")[1])
                      for k, e in emus.items()}  # fmt: skip
        self.assertEqual({s["fw_variant"] for s in status.values()}, {"buyer"})
        self.assertEqual(status["songs"]["scenes"], ",".join(gs.SCENES))
        self.assertFalse(status["trouble"]["sd_mounted"])
        self.assertTrue(status["empty"]["sd_mounted"])

    def test_the_radio_child_inherits_no_castle_knob(self) -> None:
        with mock.patch.dict(os.environ, {"CASTLE_RADIO_HOST": "x", "CASTLE_KEY": "k"}):
            env = gs.radio_env(Path("/scratch"))
        self.assertNotIn("CASTLE_RADIO_HOST", env)
        self.assertNotIn("CASTLE_KEY", env)
        self.assertEqual(env["CASTLE_RADIO_DATA"], str(Path("/scratch/radio")))


class TestPretendLan(unittest.TestCase):
    def test_only_the_stand_in_and_loopback_connect(self) -> None:
        seen: list[tuple[str, int]] = []

        def real(address: tuple[str, int], *_a: Any, **_k: Any) -> socket.socket:
            seen.append(address)
            return mock.Mock(spec=socket.socket)

        connect = gsr.guard(real, 4242)
        connect((gsr.CASTLE_IP, 80))
        connect((gsr.CASTLE_NAME + ".local", 80))
        connect(("127.0.0.1", 9))
        self.assertEqual(
            seen, [("127.0.0.1", 4242), ("127.0.0.1", 4242), ("127.0.0.1", 9)]
        )
        for far in ((gsr.CASTLE_IP, 8080), ("10.0.0.7", 80), ("github.com", 443)):
            with self.assertRaises(ConnectionRefusedError):
                connect(far)
        self.assertEqual(len(seen), 3)

    def test_the_browse_answer_is_one_castle_at_the_stand_in(self) -> None:
        found = castle_find.candidates(gsr.answers())
        self.assertEqual(found, {gsr.CASTLE_IP: gsr.CASTLE_NAME + ".local"})

    def test_the_offer_is_one_ahead(self) -> None:
        self.assertEqual(gsr.next_version("5.76"), "5.77")
        self.assertEqual(gsr.next_version("5.99"), "5.100")

    def test_castle_radio_finds_and_adopts_the_emulated_castle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, gs.castles(Path(tmp)) as emus, \
                gs.radio(Path(tmp), emus["songs"].port) as base:  # fmt: skip
            found = post(f"{base}/radio/device/find", {})["found"]
            self.assertEqual([(c["name"], c["address"], c["fw_variant"]) for c in found],
                             [("castle-a1b2c3.local", gsr.CASTLE_IP, "buyer")])  # fmt: skip
            adopted = post(f"{base}/radio/device/address", {"host": gsr.CASTLE_IP})
            self.assertEqual(adopted["host"], gsr.CASTLE_IP)
            plan = json.loads(get(f"{base}/radio/castle/update")[1])
            self.assertTrue(plan["update"])
            self.assertEqual(plan["available"], gsr.next_version(plan["current"]))
            self.assertEqual(
                json.loads(get(f"{base}/radio/first-run")[1]), {"demo": []}
            )


class TestMake(unittest.TestCase):
    def test_make_runs_the_browser_on_live_stand_ins_and_compresses_what_it_took(
        self,
    ) -> None:
        def browser(argv: list[str], input: str, text: bool, check: bool) -> None:
            plan = json.loads(input)
            self.assertEqual(len(argv), 2, "a path on the command line")
            for url in (plan["radio"], plan["portal"], *plan["owner"].values()):
                self.assertEqual(
                    get(url + ("/api/status" if url in plan["owner"].values() else ""))[
                        0
                    ],
                    200,
                )
            for stem in gs.SHOTS:
                Image.new("RGB", (20, 10), "purple").save(gs.RAW / f"{stem}.png")

        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as raw, \
                mock.patch.object(gs, "RAW", Path(raw) / "raw"), \
                mock.patch.object(gs, "bundle", return_value=Path("shots.mjs")), \
                mock.patch.object(gs.subprocess, "run", side_effect=browser):  # fmt: skip
            sizes = gs.make(Path(out))
            self.assertEqual(sorted(sizes), sorted(gs.SHOTS))
            self.assertEqual(len(list(Path(out).glob("*.png"))), len(gs.SHOTS))

    def test_a_picture_the_browser_did_not_take_is_said(self) -> None:
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as raw, \
                mock.patch.object(gs, "RAW", Path(raw) / "raw"), \
                mock.patch.object(gs, "bundle", return_value=Path("shots.mjs")), \
                mock.patch.object(gs.subprocess, "run"), \
                self.assertRaisesRegex(gs.ShotError, "did not make: portal"):  # fmt: skip
            gs.make(Path(out))

    def test_missing_tools_are_named(self) -> None:
        with mock.patch.object(gs.shutil, "which", return_value=None), \
                self.assertRaisesRegex(gs.ShotError, "node is not on PATH"):  # fmt: skip
            gs.node()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(gs, "WEB", Path(tmp)), \
                self.assertRaisesRegex(gs.ShotError, "npm ci"):  # fmt: skip
            gs.bundle()

    def test_main_checks_or_fails_in_one_line(self) -> None:
        self.assertEqual(gs.main(["--check"]), 0)
        with mock.patch.object(gs, "make", side_effect=gs.ShotError("no browser")), \
                mock.patch("sys.stderr") as err:  # fmt: skip
            self.assertEqual(gs.main([]), 1)
        err.write.assert_any_call("guide_shots: no browser")


if __name__ == "__main__":
    unittest.main()
