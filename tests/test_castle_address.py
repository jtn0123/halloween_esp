"""The castle's address in the one store — tools/castle_address.py.

"Find my castle" ends in `adopt`: the castle the owner picked becomes the
first table of devices.toml (hosts.devices_path), where every client looks
first. Held to castle_keys.py's promises — the owner's comments and other
tables survive, nothing outside a devices.toml is ever written — and to its
own: a castle that moved keeps its key and its comments, a castle found
again changes nothing, and the two writers never undo each other.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_address as ca
import castle_keys as ck
import helpers  # noqa: F401  (the sandbox scrub: CASTLE_HOST / CASTLE_DEVICES)
import hosts

OWN = """\
# the owner's own words, which must survive
[porch]
# the castle in the yard
host = "10.0.0.7"   # a reserved lease
# the name it answers to
fallbacks = ["porch.local"]
key = "porch-key"

# the bench board, on the desk
[bench]
host = "10.0.0.9"
fallbacks = [
  "bench.local",
]
key = "bench-key"
"""


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-address-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.file = self.tmp / "devices.toml"
        self.file.write_text(OWN, encoding="utf-8")
        env = mock.patch.dict(os.environ, {"CASTLE_DEVICES": str(self.file)})
        env.start()
        self.addCleanup(env.stop)

    def text(self) -> str:
        return self.file.read_text(encoding="utf-8")

    def tables(self) -> dict[str, object]:
        return tomllib.loads(self.text())


class TestAdopt(StoreCase):
    def test_a_castle_that_moved_keeps_its_key_comments_and_place(self) -> None:
        self.assertTrue(ca.adopt("10.0.0.42", "porch.local"))
        self.assertEqual(hosts.first_castle(), ["10.0.0.42", "porch.local"])
        self.assertEqual(hosts.castle_key("10.0.0.42"), "porch-key")
        self.assertNotIn("10.0.0.7", self.text(), "a moved lease is not a fallback")
        for note in ("owner's own words", "castle in the yard", "a reserved lease"):
            self.assertIn(note, self.text())
        self.assertEqual(self.tables()["bench"], tomllib.loads(OWN)["bench"])
        # Found again, nothing to say: the file is not touched.
        before = self.text()
        self.assertFalse(ca.adopt("10.0.0.42", "porch.local"))
        self.assertEqual(self.text(), before)

    def test_a_later_castle_moves_to_the_top_with_its_key_and_note(self) -> None:
        self.assertTrue(ca.adopt("10.0.0.50", "bench.local"))
        self.assertEqual(hosts.first_castle(), ["10.0.0.50", "bench.local"])
        self.assertEqual(list(self.tables()), ["bench", "porch"])
        self.assertEqual(hosts.castle_key("10.0.0.50"), "bench-key")
        text = self.text()
        self.assertLess(text.index("the bench board"), text.index("[bench]"))
        self.assertLess(text.index("[bench]"), text.index("[porch]"))
        self.assertTrue(text.startswith("# the owner's own words"))
        self.assertEqual(self.tables()["porch"], tomllib.loads(OWN)["porch"])
        # The studio's walk: the adopted castle first, the other still there.
        self.assertEqual(
            hosts.candidates()[:3], ["10.0.0.50", "bench.local", "10.0.0.7"]
        )

    def test_by_address_alone_a_known_castle_is_moved_not_copied(self) -> None:
        self.assertTrue(ca.adopt("10.0.0.9"))
        self.assertEqual(hosts.first_castle(), ["10.0.0.9", "bench.local"])
        self.assertEqual(self.text().count("[bench]"), 1)
        self.assertEqual(self.text().count("10.0.0.9"), 1)

    def test_a_new_castle_gets_a_marked_table_on_top(self) -> None:
        self.assertTrue(ca.adopt("192.168.1.40", "castle-a1b2c3.local"))
        self.assertEqual(next(iter(self.tables())), "castle-a1b2c3")
        self.assertIn(ca.MARK, self.text())
        self.assertEqual(hosts.first_castle(), ["192.168.1.40", "castle-a1b2c3.local"])
        self.assertEqual(hosts.castle_key("192.168.1.40"), "")
        for name in ("porch", "bench"):
            self.assertEqual(self.tables()[name], tomllib.loads(OWN)[name])
        # A name that is already a table's is not reused.
        ca.adopt("192.168.1.41")
        self.assertEqual(next(iter(self.tables())), "castle-1")

    def test_a_missing_store_is_created_private_with_a_header(self) -> None:
        (self.tmp / "per-user").mkdir()
        fresh = self.tmp / "per-user" / "devices.toml"
        self.assertTrue(ca.adopt("127.0.0.1:8123", "", fresh))
        self.assertTrue(fresh.read_text(encoding="utf-8").startswith(ca.NEW_FILE))
        self.assertEqual(ca.current(fresh), "127.0.0.1:8123")
        if os.name == "posix":
            self.assertEqual(fresh.stat().st_mode & 0o777, 0o600)
        empty = self.tmp / "per-user" / "spare"
        empty.mkdir()
        self.assertEqual(ca.current(empty / "devices.toml"), "")

    def test_the_repo_shaped_inventory_keeps_every_line(self) -> None:
        """The shape the tracked devices.toml has: a long header, then one
        table whose comments sit between its fields."""
        shaped = (
            "# header one\n# header two\n\n[yard]\n# the board\n"
            'host = "10.27.27.81"\n# the name, tried when the lease moves\n'
            'fallbacks = ["yard.local"]\n'
        )
        self.file.write_text(shaped, encoding="utf-8")
        self.assertFalse(ca.adopt("10.27.27.81", "yard.local"))
        self.assertTrue(ca.adopt("10.27.27.99", "yard.local"))
        self.assertEqual(self.text(), shaped.replace("10.27.27.81", "10.27.27.99"))


class TestTheTwoWriters(StoreCase):
    def test_a_key_only_table_adopted_survives_forgetting_the_key(self) -> None:
        self.file.write_text("", encoding="utf-8")
        ck.remember("192.168.1.40", "k3y")
        self.assertIn(ck.MARK, self.text())
        ca.adopt("192.168.1.40", "castle-a1b2c3.local")
        self.assertNotIn(ck.MARK, self.text())
        self.assertEqual(hosts.castle_key("castle-a1b2c3.local"), "k3y")
        ck.forget("192.168.1.40")
        self.assertEqual(hosts.first_castle(), ["192.168.1.40", "castle-a1b2c3.local"])
        self.assertEqual(hosts.castle_key("192.168.1.40"), "")

    def test_a_key_remembered_after_adopting_lands_in_the_adopted_table(self) -> None:
        ca.adopt("192.168.1.40", "castle-a1b2c3.local")
        ck.remember("castle-a1b2c3.local", "k3y")
        self.assertEqual(self.tables()["castle-a1b2c3"]["key"], "k3y")  # type: ignore[index]
        ck.forget("192.168.1.40")
        self.assertIn("[castle-a1b2c3]", self.text())


class TestRefusals(StoreCase):
    def test_bad_addresses_and_other_files_are_refused_untouched(self) -> None:
        for host, name in (("a b", ""), ("http://x/", ""), ("10.0.0.1", "x/y")):
            with self.subTest(host=host, name=name), self.assertRaises(ValueError):
                ca.adopt(host, name)
        self.assertEqual(self.text(), OWN)
        dotfile = self.tmp / ".profile"
        dotfile.write_text("export A=1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "devices.toml"):
            ca.adopt("10.0.0.1", "", dotfile)
        self.assertEqual(dotfile.read_text(encoding="utf-8"), "export A=1\n")
        self.file.write_text("[broken\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not valid TOML"):
            ca.adopt("10.0.0.1")
        self.assertEqual(self.text(), "[broken\n")

    def test_a_result_the_reader_disagrees_with_is_never_kept(self) -> None:
        with (
            mock.patch.object(ca.hosts, "first_castle", return_value=["elsewhere"]),
            self.assertRaisesRegex(ValueError, "could not be updated safely"),
        ):
            ca.adopt("10.0.0.42", "porch.local")
        self.assertEqual(self.text(), OWN)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["devices.toml"])


class TestCli(StoreCase):
    def run_main(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ca.main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_adopt_show_and_usage(self) -> None:
        self.assertEqual(self.run_main("show"), (0, "10.0.0.7\n"))
        self.assertEqual(
            self.run_main("adopt", "10.0.0.8", "porch.local"), (0, "castle saved\n")
        )
        self.assertEqual(
            self.run_main("adopt", "10.0.0.8", "porch.local"),
            (0, "already the castle\n"),
        )
        code, said = self.run_main("adopt", "a b")
        self.assertEqual(code, 1)
        self.assertIn("castle address not saved", said)
        self.assertEqual(self.run_main("nope")[0], 2)
        self.file.write_text("", encoding="utf-8")
        self.assertEqual(self.run_main("show"), (0, "no castle yet\n"))


if __name__ == "__main__":
    unittest.main()
