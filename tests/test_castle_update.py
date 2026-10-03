"""The owner's "Update castle" (tools/castle_update.py), end to end and offline:
a fake GitHub (tools/release_emu.py) serves the release, castle_emu is the
castle, and every way an update must stop is walked — a checksum that does
not match, another board, another build, nothing newer, no network, a key
needed or wrong, and a castle that comes back on the OLD firmware."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)

# isort: split
import castle_keys
import castle_update as cu
import desktop_release as rel
import release_emu
import sd_ota
from castle_emu import CastleEmu

IMAGE = b"\xe9" + bytes(range(256)) * 300  # past the castle's 64 KB floor


class UpdateCase(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"CASTLE_PRERELEASE": ""})
        env.start()
        self.addCleanup(env.stop)
        self.github = release_emu.ReleaseEmu().start()
        self.addCleanup(self.github.stop)
        self.lines: list[str] = []

    def castle(self, version: str = "5.75", **kw: object) -> CastleEmu:
        kw.setdefault("fw_variant", "buyer")
        emu = CastleEmu(port=0, scenes=["vigil"], version=version, **kw)  # type: ignore[arg-type]
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        emu.state.boot -= 3600  # an hour up: a reboot is a visible drop
        self.host = f"127.0.0.1:{emu.port}"
        return emu

    def release(
        self, tag: str = "v1.1.0", version: str = "5.76", **kw: object
    ) -> dict[str, bytes]:
        files = release_emu.firmware_release(tag, version, IMAGE, **kw)  # type: ignore[arg-type]
        self.github.publish(tag, files)
        return files

    def update(self, prerelease: bool = False) -> str:
        return cu.run(
            self.host, self.github.fetch, prerelease, self.lines.append, None, 80, 0.05
        )

    def refused(self) -> str:
        with self.assertRaises(cu.UpdateError) as caught:
            self.update()
        return str(caught.exception)

    def ota_fetched(self) -> bool:
        return any(h.endswith(".ota.bin") for h in self.github.hits)


class Updates(UpdateCase):
    def test_a_newer_release_is_flashed_and_confirmed(self) -> None:
        emu = self.castle()
        emu.boots_as = "5.76"
        self.release()
        self.assertEqual(
            self.update(), "The castle now runs firmware 5.76 (it ran 5.75)."
        )
        self.assertEqual(emu.version, "5.76")
        self.assertIn("Waiting for the castle to restart", self.lines)

    def test_up_to_date_and_newer_castles_are_left_alone(self) -> None:
        self.release(version="5.75")
        self.castle("5.75")
        self.assertIn("the newest there is", self.update())
        self.castle("5.80")
        self.assertIn("newer than v1.1.0's", self.update())
        self.assertFalse(self.ota_fetched(), "nothing to flash, nothing downloaded")

    def test_a_castle_that_comes_back_on_the_old_firmware_is_a_rollback(self) -> None:
        emu = self.castle()  # boots_as None: the image never started
        self.release()
        why = self.refused()
        self.assertIn("still runs firmware 5.75", why)
        self.assertIn("went back to the firmware it had", why)
        self.assertIn("last restart", why)
        self.assertEqual(emu.version, "5.75")

    def test_a_castle_on_a_third_version_is_said_plainly(self) -> None:
        emu = self.castle()
        emu.boots_as = "5.70"
        self.release()
        self.assertIn("came back running 5.70, not 5.76", self.refused())

    def test_a_castle_that_never_comes_back_is_said(self) -> None:
        emu = self.castle()
        self.release()

        def gone(*_args: object) -> None:
            emu.shutdown()
            emu.server_close()

        with mock.patch.object(sd_ota, "wait_back", side_effect=gone):
            self.assertIn("has not come back", self.refused())

    def test_a_castle_that_never_restarted_is_told_from_one_that_did(self) -> None:
        emu = self.castle()
        self.release()
        with (
            mock.patch.object(sd_ota, "push", return_value=None),  # lost in transit
            self.assertRaises(cu.UpdateError) as caught,
        ):
            cu.run(
                self.host, self.github.fetch, False, self.lines.append, None, 3, 0.01
            )
        why = str(caught.exception)
        self.assertIn("did not restart and still runs firmware 5.75", why)
        self.assertIn("nothing changed", why)
        self.assertEqual(emu.version, "5.75")

    def test_the_pre_release_channel_is_opt_in(self) -> None:
        emu = self.castle()
        self.release("v1.1.0", "5.76")
        self.release("v1.2.0-rc.1", "5.77")
        self.assertTrue(cu.check(self.host, self.github.fetch).update)
        self.assertEqual(cu.check(self.host, self.github.fetch).available, "5.76")
        early = cu.check(self.host, self.github.fetch, prerelease=True)
        self.assertEqual((early.available, early.tag), ("5.77", "v1.2.0-rc.1"))
        emu.boots_as = "5.77"
        self.assertIn("5.77", self.update(prerelease=True))


class Refusals(UpdateCase):
    def test_a_checksum_mismatch_sends_nothing(self) -> None:
        emu = self.castle()
        emu.boots_as = "5.76"
        files = self.release()
        ota = next(n for n in files if n.endswith(".ota.bin"))
        self.github.blobs[f"/download/v1.1.0/{ota}"] = IMAGE[:-1] + b"\0"
        why = self.refused()
        self.assertIn("checksum mismatch", why)
        self.assertIn("nothing was sent", why)
        self.assertEqual(emu.version, "5.75")

    def test_a_release_without_checksums_or_with_a_bad_descriptor_is_refused(
        self,
    ) -> None:
        self.castle()
        files = release_emu.firmware_release("v1.1.0", "5.76", IMAGE)
        self.github.publish("v1.1.0", {n: b for n, b in files.items() if n != rel.SUMS})
        self.assertIn("refusing unverified files", self.refused())
        files = release_emu.firmware_release("v1.2.0", "5.76", IMAGE)
        about = next(n for n in files if n.endswith(".json"))
        files[about] = files[about].replace(b'"5.76"', b'"5.99"')  # after the sums
        self.github.publish("v1.2.0", files)
        self.assertIn("checksum mismatch", self.refused())
        self.assertFalse(self.ota_fetched())

    def test_another_board_is_never_offered_an_image(self) -> None:
        self.castle(board="wroom-s3-8m2p")
        self.release()
        self.assertIn(
            "no firmware for this castle's board (wroom-s3-8m2p)", self.refused()
        )
        self.github.publish(
            "v1.2.0",
            release_emu.firmware_release("v1.2.0", "5.76", IMAGE, board="wroom-s3-8m2p")
            | {"castle-fw-wroom-s3-8m2p-v1.2.0.json": b""},
        )
        self.assertIn("checksum mismatch", self.refused())

    def test_a_descriptor_for_another_board_or_schema_is_refused(self) -> None:
        self.castle()
        changes: tuple[tuple[dict[str, object], str], ...] = (
            ({"board": "wroom-s3-8m2p"}, "not this castle's board"),
            ({"schema": 2}, "update Castle Tools first"),
            ({"version": "next"}, "names no firmware version"),
            ({"ota": "../../evil.bin"}, "not updating"),
        )
        for n, (change, words) in enumerate(changes):
            tag = f"v1.1.{n}"
            files = release_emu.firmware_release(tag, "5.76", IMAGE)
            name = next(f for f in files if f.endswith(".json"))
            files[name] = json.dumps({**json.loads(files[name]), **change}).encode()
            del files[rel.SUMS]
            self.github.publish(tag, {**files, rel.SUMS: release_emu.sums(files)})
            with self.subTest(change=change):
                self.assertIn(words, self.refused())
        self.assertFalse(self.ota_fetched())

    def test_a_yard_castle_is_never_handed_the_buyer_build(self) -> None:
        self.castle(fw_variant="yard")
        self.release()
        why = self.refused()
        self.assertIn("runs the yard build", why)
        self.assertIn("never swaps", why)

    def test_no_network_is_said_and_sends_nothing(self) -> None:
        emu = self.castle()
        self.release()
        self.github.stop()  # GitHub is unreachable: nothing listens there now
        self.assertIn("cannot reach GitHub", self.refused())
        self.assertEqual(emu.version, "5.75")

    def test_a_keyed_castle_needs_its_key_before_the_download(self) -> None:
        emu = self.castle()
        emu.key = b"s3cret"
        emu.boots_as = "5.76"
        self.release()
        self.assertEqual(self.refused(), castle_keys.KEY_REQUIRED)
        with mock.patch.dict(os.environ, {"CASTLE_KEY": "wrong"}):
            self.assertIn("not its key", self.refused())
        self.assertFalse(self.ota_fetched())
        with mock.patch.dict(os.environ, {"CASTLE_KEY": "s3cret"}):
            self.assertIn("now runs firmware 5.76", self.update())

    def test_the_castles_own_refusal_is_reported(self) -> None:
        self.castle()
        small = b"\xe9" + b"\0" * 999
        self.github.publish(
            "v1.1.0", release_emu.firmware_release("v1.1.0", "5.76", small)
        )
        why = self.refused()
        self.assertIn("refused the new firmware (400: implausible image size)", why)
        self.assertIn("still runs 5.75", why)

    def test_a_castle_that_answers_without_flashing_is_said(self) -> None:
        emu = self.castle()
        self.release()
        with mock.patch.object(sd_ota, "push", return_value={"flashed": False}):
            why = self.refused()
        self.assertIn("did not take the new firmware", why)
        self.assertIn("still runs 5.75", why)
        self.assertEqual(emu.version, "5.75")

    def test_a_castle_that_cannot_say_what_it_is_is_not_guessed_at(self) -> None:
        self.host = "127.0.0.1:9"  # nothing listens on discard
        self.assertIn("is not answering", self.refused())
        with mock.patch.object(cu, "castle_status", return_value={"version": "5.60"}):
            self.assertIn("too old to say which board", self.refused())


class CommandLine(UpdateCase):
    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch.object(rel, "http_fetch", self.github.fetch),
        ):
            code = cu.main([*argv, "--host", self.host])
        return code, out.getvalue(), err.getvalue()

    def test_check_and_update(self) -> None:
        emu = self.castle()
        self.release()
        code, out, _ = self.main("check")
        self.assertEqual(code, 0)
        self.assertIn("Firmware 5.76 (v1.1.0) is ready — the castle runs 5.75.", out)
        emu.version = "5.76"
        code, out, _ = self.main("update")
        self.assertEqual(code, 0)
        self.assertIn("nothing to update", out)
        emu.fw_variant = "yard"
        code, _, err = self.main("update")
        self.assertEqual(code, 1)
        self.assertIn("never swaps", err)


if __name__ == "__main__":
    unittest.main()
