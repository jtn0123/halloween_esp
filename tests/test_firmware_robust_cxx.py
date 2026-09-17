"""The v5.61 robustness findings, run against the real C.

Four of the five fixes in v5.61 are about what the castle does when
something goes wrong underneath it — an OTA burning flash while a song is
being streamed (A1), a card that stops answering half way through a
transfer (A8), an upload holding the one httpd task (A9), a rename that
fails after the previous copy has been moved aside (A11). None of them can
be reached by asking the castle nicely, so the harness's platform layer
grows the faults: tests/cxx/shim/castle_shim_fs.h can make a read fail
after N bytes and a `.part` rename refuse, and web_check seeds the quiesce
flag sd_web_ota.h raises.

These are C-only on purpose, and say so where it matters: the emulator has
no second HTTP port, no flash to burn and a host filesystem that does not
NAK a sector. What the two castles DO share — /api/health's new counter,
the previous copy surviving a failed rename — is checked on both sides,
here and in tests/test_firmware_contract.py.

The fifth finding (A3, the FATFS open-file budget) is a number in
firmware/sd_audio.h with a paragraph of arithmetic behind it; it is read
out of the source at the bottom of this file, because nothing a host test
can open would ever exhaust it.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_source import FUNCS, SD_STREAM, grab
from firmware_web_harness import WebPairCase


class TestQuiesceGate(WebPairCase):
    """A1: while flash is being written, the stream server steps aside.

    castle_sd::g_quiesce is the "cache is suspended, nothing else may run"
    flag sd_web_ota.h raises for the length of an upload. Every other task
    on the castle learned to read it years ago; the one that serves audio —
    2 MB off the SPI card, at the same priority as the decoder — never did.
    """

    env: ClassVar[dict[str, str]] = {"CASTLE_QUIESCE": "1"}

    def test_the_stream_server_refuses_a_card_read_while_flash_burns(self) -> None:
        r = self.pair.c.http("GET", b"/sd/wicked_winds.mp3", port=8080)
        self.assertEqual(r.status, 503)
        self.assertEqual(r.body, "updating — card reads are paused".encode())

    def test_the_control_plane_is_not_gagged_by_it(self) -> None:
        """The whole point of the OTA quiesce is that the castle stays
        reachable while it updates — the desk polls it to watch. The gate
        is the stream server's alone."""
        self.assertEqual(self.pair.c.http("GET", b"/api/status").status, 200)
        self.assertEqual(self.pair.c.http("GET", b"/api/health").status, 200)
        self.assertEqual(self.pair.c.http("GET", b"/sd/wicked_winds.mp3").status, 200)


class TestTornTransfer(WebPairCase):
    """A8: a read that fails 2 KB into a 4 KB file."""

    env: ClassVar[dict[str, str]] = {"CASTLE_SD_FAIL_AFTER": "2048"}

    def test_a_dying_card_is_not_framed_as_a_short_song(self) -> None:
        r = self.pair.c.http("GET", b"/sd/wicked_winds.mp3")
        # The 200 and its headers are already on the wire when the read
        # fails — there is no taking them back. What CAN be taken back is
        # the terminator, and without it every HTTP client reports a
        # truncated body instead of a complete one.
        self.assertEqual(r.status, 200)
        self.assertEqual(len(r.body), 2048)
        self.assertEqual(r.headers.get("X-Castle-Aborted"), "1")
        # ...and the castle counts it, so /api/health answers "is this card
        # going bad" with a number.
        health = json.loads(self.pair.c.http("GET", b"/api/health").body)
        self.assertGreaterEqual(health["sd_read_errors"], 1)

    def test_the_site_route_tears_down_the_same_way(self) -> None:
        """Every route that streams the card goes through send_sd_file, so
        the desk page dies the same way the song does — a page that stops
        half way is a browser error, not half a page."""
        (self.pair.card_c / "site" / "big.js").write_bytes(b"z" * 5000)
        r = self.pair.c.http("GET", b"/site/big.js")
        self.assertEqual(len(r.body), 2048)
        self.assertEqual(r.headers.get("X-Castle-Aborted"), "1")


class TestDeadCard(WebPairCase):
    """A8's other leg: the read fails before a single byte has gone out,
    so the status line is still ours to choose."""

    env: ClassVar[dict[str, str]] = {"CASTLE_SD_FAIL_AFTER": "0"}

    def test_a_read_that_fails_at_once_is_a_500(self) -> None:
        r = self.pair.c.http("GET", b"/sd/wicked_winds.mp3")
        self.assertEqual((r.status, r.body), (500, b"card read failed"))
        self.assertNotIn("X-Castle-Aborted", r.headers)
        health = json.loads(self.pair.c.http("GET", b"/api/health").body)
        self.assertGreaterEqual(health["sd_read_errors"], 1)

    def test_a_desk_page_that_cannot_be_read_is_not_a_404(self) -> None:
        """404 would send the desk looking for a file that is right there
        — and, for /, quietly serve the flash fallback instead, which is
        the same lie one level up."""
        self.assertEqual(self.pair.c.http("GET", b"/site/app.js").status, 500)
        self.assertEqual(self.pair.c.http("GET", b"/").status, 500)


class TestRenameIntoPlace(WebPairCase):
    """A11: the rename that puts the upload in place fails."""

    env: ClassVar[dict[str, str]] = {"CASTLE_RENAME_PART_FAILS": "1"}

    def test_a_failed_rename_leaves_the_previous_copy_playable(self) -> None:
        card = self.pair.card_c
        good = b"the previous good copy" * 64
        (card / "keep.mp3").write_bytes(good)  # seeded, not uploaded
        r = self.pair.c.http("PUT", b"/api/files/keep.mp3", b"the new one" * 64)
        self.assertEqual((r.status, r.body), (500, b"rename failed"))
        # The show's track is still there, byte for byte...
        self.assertEqual((card / "keep.mp3").read_bytes(), good)
        # ...and nothing was left lying around for the next upload to trip
        # over: no sidecar, no `.old` shadow of a file that still exists.
        self.assertFalse((card / "keep.mp3.part").exists())
        self.assertFalse((card / "keep.mp3.old").exists())

    def test_a_first_upload_of_a_name_still_fails_cleanly(self) -> None:
        """Nothing to preserve, so nothing to restore — and no empty file
        left behind under the real name."""
        r = self.pair.c.http("PUT", b"/api/files/fresh.mp3", b"x" * 100)
        self.assertEqual((r.status, r.body), (500, b"rename failed"))
        for card in (self.pair.card_c,):
            self.assertFalse((card / "fresh.mp3").exists())
            self.assertFalse((card / "fresh.mp3.part").exists())


class TestEmulatorKeepsThePreviousCopy(unittest.TestCase):
    """A11 on the other castle. The emulator writes a real host file, so
    the failure has to be injected the way Python injects failures."""

    def test_a_rename_that_raises_leaves_the_old_file_alone(self) -> None:
        import castle_emu
        import castle_link as cl

        card = Path(tempfile.mkdtemp(prefix="a11-emu-"))
        good = b"the previous good copy"
        (card / "keep.mp3").write_bytes(good)
        emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil"])
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        real = Path.rename

        def refuse(self: Path, target: str | Path) -> Path:
            if self.suffix == ".part":
                raise OSError("no room in the FAT root directory")
            return real(self, target)

        with mock.patch.dict("os.environ", {"CASTLE_HOST": f"127.0.0.1:{emu.port}"}):
            with mock.patch.object(Path, "rename", refuse):
                code, out, _ = cl.forward("PUT", "/api/files/keep.mp3", b"the new one")
        self.assertEqual((code, out), (500, b"rename failed"))
        self.assertEqual((card / "keep.mp3").read_bytes(), good)
        self.assertFalse((card / "keep.mp3.part").exists())
        self.assertFalse((card / "keep.mp3.old").exists())


class TestOpenFileBudget(unittest.TestCase):
    """A3: how many files FATFS will hold open on the card at once.

    Four was the ESP-IDF example's number, and the castle can legitimately
    have four open before anything goes wrong — the stream server's track,
    a browser's page, the upload worker's sidecar and the boot log. The
    fifth fopen fails, and it fails as ENOENT-shaped nonsense in the middle
    of a show.
    """

    def test_the_mount_reserves_more_than_the_castle_can_open(self) -> None:
        src = (ROOT / "firmware" / "sd_audio.h").read_text()
        default = int(grab(r"int max_files = (\d+)\)", src))
        self.assertGreaterEqual(default, 8, "A3: the open-file budget shrank")
        # The number is only half the finding: the RAM it costs is paid at
        # mount and never given back, so it has to be written down where
        # the next person to raise it will read it.
        doc = src[: src.index("inline bool mount(")]
        self.assertIn("A3", doc)
        self.assertIn("RAM", doc)
        self.assertTrue(re.search(r"\d+(\.\d+)? KB", doc), "no RAM cost quoted")


class TestUploadIsOffTheControlTask(unittest.TestCase):
    """A9, read out of the firmware: the shape of the hand-off.

    What it DOES is checked where it can be watched — the emulator answers
    a status poll mid-upload now (tests/test_emu_modes.py), and the C
    harness refuses to exit if any async request was left uncompleted,
    which is the failure that eventually stops the server accepting
    connections at all.
    """

    def test_h_put_validates_here_and_writes_there(self) -> None:
        body = FUNCS["h_put"]
        self.assertIn("upload_offload(req, path)", body)
        self.assertNotIn("write_body(req", body)
        # The cheap refusals must NOT move to the worker: a bad name has to
        # come back as fast as it always did.
        for guard in ('"empty body"', '"bad filename"', '"site file too large"'):
            self.assertIn(guard, body)

    def test_the_worker_owns_the_socket_and_gives_it_back(self) -> None:
        offload = FUNCS["upload_offload"]
        self.assertIn("httpd_req_async_handler_begin(req, &copy)", offload)
        # Queued with a zero wait — blocking here would be the very stall
        # the worker exists to remove.
        self.assertIn("xQueueSend(g_upload_q, &job, 0)", offload)
        pump = FUNCS["upload_pump"]
        self.assertIn("httpd_req_async_handler_complete(job.req)", pump)
        # And a castle with no worker still uploads, the pre-v5.61 way.
        self.assertIn("return write_body(req, path.c_str());", offload)

    def test_the_worker_runs_below_audio_and_above_the_show(self) -> None:
        start = FUNCS["upload_start"]
        call = grab(r"(?s)xTaskCreatePinnedToCore\((.*?)\) != pdPASS", start)
        args = [a.strip() for a in " ".join(call.split()).split(",")]
        self.assertEqual(args[0], "upload_task")
        self.assertEqual(int(args[4]), 4)  # 5 is the audio band, 1 the loop
        self.assertEqual(int(args[6]), 0)  # core 0, off the loop's core


class TestStreamTaskPriority(unittest.TestCase):
    """A1's other half: the stream server's own place in the scheduler."""

    def test_the_stream_server_is_below_the_audio_band(self) -> None:
        prio = int(grab(r"cfg\.task_priority = (\d+);", SD_STREAM))
        self.assertEqual(prio, 4)
        self.assertEqual(int(grab(r"cfg\.core_id = (\d+);", SD_STREAM)), 0)

    def test_the_gate_is_what_the_server_registers(self) -> None:
        self.assertIn("u.handler = gate;", SD_STREAM)
        self.assertIn("castle_sd::g_quiesce", SD_STREAM)


if __name__ == "__main__":
    unittest.main()
