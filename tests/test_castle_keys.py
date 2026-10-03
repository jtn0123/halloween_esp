"""The castle key store's one writer — tools/castle_keys.py.

The store is devices.toml (hosts.devices_path), which the owner also edits
by hand and which, in a dev checkout, is tracked in a public repo. So the
writer is held to four promises: it remembers a key where hosts.py (and
castle-core's hosts.rs) will find it; it leaves every other line of the
owner's file alone, comments included; forgetting undoes remembering
exactly; and no key ever reaches an error, an argument or a commit.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_keys as ck
import helpers  # noqa: F401  (the sandbox scrub: CASTLE_KEY / CASTLE_DEVICES)
import hosts

OWN = """\
# the owner's own words, which must survive
[porch]
# the castle in the yard
host = "10.0.0.7"   # a reserved lease
fallbacks = ["porch.local"]

[bench]
host = "10.0.0.9"
key = "old-key"
"""


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-keys-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.file = self.tmp / "devices.toml"
        self.file.write_text(OWN, encoding="utf-8")
        env = mock.patch.dict(os.environ, {"CASTLE_DEVICES": str(self.file)})
        env.start()
        self.addCleanup(env.stop)

    def _text(self) -> str:
        return self.file.read_text(encoding="utf-8")


class TestRemember(StoreCase):
    def test_a_key_lands_under_the_castles_own_table_and_round_trips(self) -> None:
        for key in ('q"uo\\te#1', "plain", "!~", "k" * hosts.KEY_MAX):
            with self.subTest(key=key):
                ck.remember("porch.local", key)  # a fallback names the table
                self.assertEqual(hosts.castle_key("10.0.0.7"), key)
                self.assertEqual(ck.stored_key("porch.local"), key)
                self.assertIn("# the castle in the yard", self._text())
                self.assertEqual(self._text().count("[porch]"), 1)
        ck.forget("10.0.0.7")
        self.assertEqual(self._text(), OWN)

    def test_an_existing_key_line_is_replaced_not_duplicated(self) -> None:
        ck.remember("10.0.0.9", "new-key")
        self.assertEqual(hosts.castle_key("10.0.0.9"), "new-key")
        self.assertEqual(self._text().count("key ="), 1)
        ck.forget("10.0.0.9")
        self.assertEqual(hosts.castle_key("10.0.0.9"), "")
        self.assertIn("[bench]", self._text())  # the owner's table stays

    def test_an_unknown_castle_gets_a_marked_table_forget_takes_whole(self) -> None:
        ck.remember("127.0.0.1:8093", "emu-key")
        self.assertIn(ck.MARK, self._text())
        self.assertEqual(hosts.castle_key("127.0.0.1:8093"), "emu-key")
        ck.forget("127.0.0.1:8093")
        self.assertEqual(self._text(), OWN)
        ck.forget("127.0.0.1:8093")  # nothing to forget is not an error
        self.assertEqual(self._text(), OWN)

    def test_a_missing_file_is_created_private(self) -> None:
        # The folder is the app's (both make theirs at start-up); the file is
        # this module's to create.
        (self.tmp / "per-user").mkdir()
        fresh = self.tmp / "per-user" / "devices.toml"
        ck.remember("10.1.1.1", "k3y", fresh)
        self.assertEqual(hosts.stored_key("10.1.1.1", fresh), "k3y")
        if os.name == "posix":
            self.assertEqual(fresh.stat().st_mode & 0o777, 0o600)

    def test_the_store_can_move_but_never_become_another_file(self) -> None:
        """CASTLE_DEVICES chooses the FOLDER: a target that is not a
        devices.toml — a dotfile, a .toml of another name, a symlink that
        leads to one — or that sits in a folder nobody made, is refused
        with nothing read or written."""
        dotfile = self.tmp / ".profile"
        dotfile.write_text("export PATH=/usr/bin\n", encoding="utf-8")
        other = self.tmp / "settings.toml"
        other.write_text('[a]\nhost = "h"\n', encoding="utf-8")
        targets = [dotfile, other, self.tmp / "nowhere" / "devices.toml"]
        if os.name == "posix":
            link = self.tmp / "linked" / "devices.toml"
            link.parent.mkdir()
            link.symlink_to(dotfile)
            targets.append(link)
        for target in targets:
            # None: the store named by CASTLE_DEVICES, as the apps reach it.
            for given in (target, None):
                with (
                    self.subTest(target=target.name, given=given is not None),
                    mock.patch.dict(os.environ, {"CASTLE_DEVICES": str(target)}),
                ):
                    with self.assertRaises(ValueError) as cm:
                        ck.remember("10.0.0.7", "s3cret", given)
                    self.assertIn("devices.toml", str(cm.exception))
                    self.assertNotIn("s3cret", str(cm.exception))
        self.assertEqual(dotfile.read_text(encoding="utf-8"), "export PATH=/usr/bin\n")
        self.assertEqual(other.read_text(encoding="utf-8"), '[a]\nhost = "h"\n')
        self.assertFalse((self.tmp / "nowhere").exists())
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir() if p.name.startswith(".devices")),
            [],
        )

    def test_a_failed_read_back_leaves_the_file_and_no_probe(self) -> None:
        with (
            mock.patch.object(ck.hosts, "stored_key", return_value="not-it"),
            self.assertRaises(ValueError) as cm,
        ):
            ck.remember("10.0.0.7", "s3cret")
        self.assertIn("could not be updated safely", str(cm.exception))
        self.assertEqual(self._text(), OWN)
        self.assertEqual(list(self.tmp.glob(".devices.toml.*.tmp")), [])

    def test_refusals_name_the_rule_never_the_key(self) -> None:
        with self.assertRaises(ValueError) as cm:
            ck.remember("10.0.0.7", "two words")
        self.assertNotIn("two words", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            ck.remember("bad host/x", "s3cret")
        self.assertEqual(str(cm.exception), "not a castle address")
        self.assertEqual(self._text(), OWN)
        self.file.write_text("[porch\nhost = ", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            ck.remember("10.0.0.7", "s3cret")
        self.assertIn("not valid TOML", str(cm.exception))
        self.assertEqual(self._text(), "[porch\nhost = ")  # never "repaired"

    def test_the_pin_is_castle_key_set_at_all(self) -> None:
        self.assertFalse(ck.pinned())
        with mock.patch.dict(os.environ, {"CASTLE_KEY": ""}):
            self.assertTrue(ck.pinned())
            # The file half is what the store holds, pinned or not.
            self.assertEqual(ck.stored_key("10.0.0.9"), "old-key")


class TestAct(StoreCase):
    """The castle's answers `act` has to word — the happy paths and the
    emulator walk are demo/castle-radio/test_castle_key.py's."""

    def answering(self, code: int, body: bytes = b"") -> AbstractContextManager[Any]:
        return mock.patch.object(ck, "ask", return_value=(code, body))

    def test_what_the_castle_said_becomes_a_status_and_a_sentence(self) -> None:
        cases = [
            ("use", 401, 401, ck.WRONG_KEY),
            ("set", 401, 401, ck.KEY_REQUIRED),
            ("set", 404, 409, ck.OLD_FIRMWARE),
            ("set", 400, 400, "bad key"),
            ("clear", 500, 502, "castle answered 500"),
        ]
        for action, castle, status, said in cases:
            with (
                self.subTest(action=action, castle=castle),
                self.answering(castle, b"bad key\n"),
            ):
                with self.assertRaises(ck.Refusal) as cm:
                    ck.act("10.0.0.7", action, "s3cret")
                self.assertEqual(
                    (cm.exception.status, str(cm.exception)), (status, said)
                )
        self.assertEqual(self._text(), OWN, "nothing the castle refused is remembered")

    def test_a_store_that_cannot_follow_says_the_castle_already_changed(self) -> None:
        self.file.write_text("[porch\n", encoding="utf-8")
        with self.answering(200), self.assertRaises(ck.Refusal) as cm:
            ck.act("10.0.0.7", "set", "s3cret")
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("the castle took it", str(cm.exception))
        self.assertNotIn("s3cret", str(cm.exception))

    def test_an_unreachable_castle_never_names_the_url(self) -> None:
        with self.assertRaises(OSError) as cm:
            ck.ask("127.0.0.1:9", "/api/key?new=s3cret", "", timeout=2)
        self.assertIn("castle not reachable", str(cm.exception))
        self.assertNotIn("s3cret", str(cm.exception))

    def test_set_sends_the_held_key_and_the_new_one_percent_encoded(self) -> None:
        with self.answering(200) as asked:
            ck.act("10.0.0.9", "set", "n&w=1")
        asked.assert_called_once_with("10.0.0.9", "/api/key?new=n%26w%3D1", "old-key")
        self.assertEqual(hosts.castle_key("10.0.0.9"), "n&w=1")
        with self.assertRaises(ck.Refusal):
            ck.act("10.0.0.9", "reveal")


class TestSdSyncSaysIt(StoreCase):
    """sd_sync (the publish the desk runs, and `make ota`) sends the stored
    key, and a refusal ends it with the sentence every app shows."""

    def test_against_a_keyed_emulator(self) -> None:
        import castle_emu
        import sd_sync

        card = self.tmp / "card"
        card.mkdir()
        emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil"])
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        emu.key = b"s3cret"
        host = f"127.0.0.1:{emu.port}"
        with self.assertRaises(SystemExit) as cm:
            sd_sync.api(host, "POST", "/api/pir?armed=1")
        self.assertEqual(str(cm.exception), ck.KEY_REQUIRED)
        # `make ota` on a keyed castle: the refusal is the key, not "is this
        # build for the chip at that address?", and nothing is flashed.
        image = self.tmp / "app.bin"
        image.write_bytes(b"\xe9" + bytes(70 * 1024))
        with (
            self.assertRaises(SystemExit) as cm,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sd_sync.sd_ota.flash(host, [str(image)], sd_sync.api, ROOT)
        self.assertEqual(str(cm.exception), ck.KEY_REQUIRED)
        self.assertFalse(emu.quiesce)
        ck.remember(host, "s3cret")
        self.assertEqual(
            json.loads(sd_sync.api(host, "POST", "/api/pir?armed=1")), {"queued": True}
        )


#: Where each refusal is copied, by its name here.
COPIES = {
    "core/src/studio_key.rs": (
        "KEY_REQUIRED",
        "WRONG_KEY",
        "BAD_KEY",
        "PINNED",
        "OLD_FIRMWARE",
    ),
    "web/src/castle_act.ts": ("KEY_REQUIRED",),
    "demo/castle-radio/castle-direct.js": (
        "KEY_REQUIRED",
        "WRONG_KEY",
        "BAD_KEY",
        "OLD_FIRMWARE",
    ),
}


class TestOneSentence(unittest.TestCase):
    """The refusals are COPIED, not imported — the Rust studio, the desk and
    the castle-served page cannot import Python — so every copy is held to
    this module's words, and "enter it in Settings" reads the same in each."""

    def test_every_copy_says_the_same_words(self) -> None:
        for rel, names in COPIES.items():
            text = (ROOT / rel).read_text(encoding="utf-8")
            # Rust's line-continuation and JS's escaped quote, undone.
            text = re.sub(r"\\\n\s*", "", text).replace("\\'", "'")
            for name in names:
                with self.subTest(file=rel, message=name):
                    self.assertIn(getattr(ck, name), text)


class TestCli(StoreCase):
    """The door the studio uses: the key on stdin, the reason on stderr."""

    def run_cli(self, *args: str, stdin: str = "") -> tuple[int, str]:
        err = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO(stdin)),
            mock.patch.object(sys, "stderr", err),
        ):
            return ck.main(list(args)), err.getvalue()

    def test_remember_and_forget_through_stdin(self) -> None:
        self.assertEqual(self.run_cli("remember", "10.0.0.7", stdin="s3cret\n")[0], 0)
        self.assertEqual(hosts.castle_key("10.0.0.7"), "s3cret")
        self.assertEqual(self.run_cli("forget", "10.0.0.7")[0], 0)
        self.assertEqual(self._text(), OWN)

    def test_a_bad_key_fails_without_saying_it(self) -> None:
        code, err = self.run_cli("remember", "10.0.0.7", stdin="not a key\n")
        self.assertEqual(code, 1)
        self.assertIn("castle key not saved", err)
        self.assertNotIn("not a key", err)

    def test_usage(self) -> None:
        self.assertEqual(self.run_cli("remember")[0], 2)
        self.assertEqual(self.run_cli()[0], 2)


class TestStagedGuard(unittest.TestCase):
    """The repo is public and devices.toml is tracked: githooks/pre-commit
    refuses a staged one that carries a key."""

    def test_what_counts_as_a_key(self) -> None:
        self.assertFalse(ck.holds_key(OWN.replace('key = "old-key"\n', "")))
        self.assertTrue(ck.holds_key(OWN))
        self.assertFalse(ck.holds_key('[a]\nhost = "h"\nkey = ""\n'))
        # Unparseable: judged line by line, erring towards "it does".
        self.assertTrue(ck.holds_key('[a\nkey = "x"\n'))
        self.assertFalse(ck.holds_key("[a\nkey = ''\n"))

    def test_the_hook_runs_it(self) -> None:
        hook = (ROOT / "githooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("tools/castle_keys.py check-staged", hook)

    def test_a_staged_key_is_refused_and_a_clean_file_is_not(self) -> None:
        repo = Path(tempfile.mkdtemp(prefix="castle-keys-git-"))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)

        def git(*a: str) -> None:
            subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

        git("init", "-q")
        (repo / "devices.toml").write_text('[a]\nhost = "h"\n', encoding="utf-8")
        git("add", "devices.toml")
        here = os.getcwd()
        os.chdir(repo)
        try:
            self.assertEqual(ck.check_staged(), 0)
            (repo / "devices.toml").write_text(
                '[a]\nhost = "h"\nkey = "s3cret"\n', encoding="utf-8"
            )
            git("add", "devices.toml")
            err = io.StringIO()
            with mock.patch.object(sys, "stderr", err):
                self.assertEqual(ck.check_staged(), 1)
            self.assertIn("devices.toml", err.getvalue())
            self.assertNotIn("s3cret", err.getvalue())
        finally:
            os.chdir(here)


if __name__ == "__main__":
    unittest.main()
