"""Which castle a tool talks to: one resolver, three sources, a fixed order.

hosts.py exists because "10.27.27.7" was hardcoded in three tools and one
muscle memory. The order is the contract — explicit argument, then
CASTLE_HOST, then the first entry in devices.toml — and a tool that got it
wrong would drive the wrong board on the night. No network anywhere here:
the table is a tempdir TOML and the env is patched per test.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (clears CASTLE_* so the env here is ours)
import hosts
from studio_case import HostEnv

TABLE = """
# comments and non-device tables are ignored
[porch]
host = "10.0.0.7"
fallbacks = ["10.0.0.8"]

[bench]
host = "10.0.0.9"

[notes]
text = "no host key — not a device"
"""


class HostCase(HostEnv, unittest.TestCase):
    """A devices.toml of our own and an environment we can scribble on: both
    classes below want the same two, and the resolver reads both."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-hosts-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.toml = self.tmp / "devices.toml"
        self.toml.write_text(TABLE)
        p = mock.patch.object(hosts, "DEVICES", self.toml)
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {}, clear=False)
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("CASTLE_HOST", None)


class TestResolve(HostCase):
    def test_an_explicit_ip_wins_over_everything(self) -> None:
        self.host_env("10.9.9.9")
        self.assertEqual(hosts.resolve("192.168.1.5"), "192.168.1.5")

    def test_an_explicit_host_port_wins_like_an_ip(self) -> None:
        # The emulator chain publishes to 127.0.0.1:<port>; refusing it as
        # "not an IP" once broke the studio's auto-publish (B5 follow-up).
        self.assertEqual(hosts.resolve("127.0.0.1:8093"), "127.0.0.1:8093")
        self.assertEqual(
            hosts.maybe_host(["10.1.1.1:8080", "ls"]), ("10.1.1.1:8080", ["ls"])
        )

    def test_an_explicit_name_is_looked_up_in_the_table(self) -> None:
        self.host_env("10.9.9.9")
        self.assertEqual(hosts.resolve("bench"), "10.0.0.9")

    def test_an_unknown_name_says_what_is_known(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            hosts.resolve("attic")
        self.assertIn("attic", str(cm.exception))
        self.assertIn("porch", str(cm.exception))
        self.assertIn("bench", str(cm.exception))

    def test_env_ip_beats_the_table(self) -> None:
        self.host_env("10.9.9.9")
        self.assertEqual(hosts.resolve(), "10.9.9.9")

    def test_env_name_is_looked_up_in_the_table(self) -> None:
        self.host_env("bench")
        self.assertEqual(hosts.resolve(), "10.0.0.9")

    def test_env_hostname_passes_through_unchanged(self) -> None:
        # castle_link accepts host:port and comma lists; the resolver must
        # not mangle a value it does not recognise.
        self.host_env("127.0.0.1:8093")
        self.assertEqual(hosts.resolve(), "127.0.0.1:8093")

    def test_first_table_entry_is_the_default(self) -> None:
        self.assertEqual(hosts.resolve(), "10.0.0.7")

    def test_tables_without_a_host_are_not_devices(self) -> None:
        self.assertEqual(hosts._table(), {"porch": "10.0.0.7", "bench": "10.0.0.9"})

    def test_nothing_anywhere_is_a_clear_error(self) -> None:
        self.toml.unlink()
        self.assertEqual(hosts._table(), {})
        with self.assertRaises(SystemExit) as cm:
            hosts.resolve()
        self.assertIn("CASTLE_HOST", str(cm.exception))
        self.assertIn("devices.toml", str(cm.exception))

    def test_empty_env_falls_through_to_the_table(self) -> None:
        self.host_env("")
        self.assertEqual(hosts.resolve(), "10.0.0.7")


class TestCandidates(TestResolve):
    """The ordered list castle_link walks: env -> entry host -> fallbacks."""

    def test_table_order_is_entry_then_its_fallbacks(self) -> None:
        self.assertEqual(hosts.candidates(), ["10.0.0.7", "10.0.0.8", "10.0.0.9"])

    def test_env_is_a_comma_list_and_names_expand(self) -> None:
        self.host_env("1.1.1.1, porch ,127.0.0.1:8093")
        self.assertEqual(
            hosts.candidates(), ["1.1.1.1", "10.0.0.7", "10.0.0.8", "127.0.0.1:8093"]
        )

    def test_empty_env_means_no_castle(self) -> None:
        self.host_env("")
        self.assertEqual(hosts.candidates(), [])

    def test_an_explicit_name_expands_and_an_ip_stands_alone(self) -> None:
        self.host_env("9.9.9.9")
        self.assertEqual(hosts.candidates("porch"), ["10.0.0.7", "10.0.0.8"])
        self.assertEqual(hosts.candidates("10.5.5.5"), ["10.5.5.5"])

    def test_resolve_is_the_first_candidate(self) -> None:
        for env in ("bench", "1.2.3.4,porch", None):
            if env is None:
                self.host_env(None)
            else:
                self.host_env(env)
            self.assertEqual(hosts.resolve(), hosts.candidates()[0], env)

    def test_no_file_is_an_empty_list(self) -> None:
        self.toml.unlink()
        self.assertEqual(hosts.candidates(), [])
        (self.toml).write_text("host = = =\n")
        self.assertEqual(hosts.candidates(), [])


class TestMaybeHost(HostCase):
    def test_a_leading_command_keeps_argv_and_resolves_the_default(self) -> None:
        self.assertEqual(hosts.maybe_host(["status"]), ("10.0.0.7", ["status"]))
        self.assertEqual(
            hosts.maybe_host(["push", "a.mp3"]), ("10.0.0.7", ["push", "a.mp3"])
        )

    def test_a_leading_host_is_popped(self) -> None:
        self.assertEqual(hosts.maybe_host(["10.1.1.1", "ls"]), ("10.1.1.1", ["ls"]))
        self.assertEqual(hosts.maybe_host(["bench", "ls"]), ("10.0.0.9", ["ls"]))

    def test_no_argv_at_all_still_resolves(self) -> None:
        self.host_env("10.2.2.2")
        self.assertEqual(hosts.maybe_host([]), ("10.2.2.2", []))

    def test_every_tool_command_is_known(self) -> None:
        """A command missing from the set would be resolved AS a host name."""
        for cmd in (
            "status",
            "health",
            "ls",
            "purge",
            "push",
            "scenes",
            "site",
            "ota",
            "rm",
            "play",
            "bootlog",
            "list",
            "press",
            "set",
            "watch",
        ):
            host, rest = hosts.maybe_host([cmd])
            self.assertEqual((host, rest), ("10.0.0.7", [cmd]), cmd)


if __name__ == "__main__":
    unittest.main()
