"""The release channel (tools/release_channel.py): stable for every owner,
pre-releases only behind the hidden opt-in — and the same rule wherever an
update is offered. The tag table and the opt-in table are READ from
desktop/src-tauri/src/channel.rs, so the app's own updater and the Python
tools (the installer's --update, the launcher's notice, the castle update)
cannot drift apart; the release workflow's tag check is held to it too."""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)

# isort: split
import desktop_env as de
import desktop_launch as dl
import desktop_lifecycle as life
import desktop_release as rel
import release_assets as ra
import release_channel as channel
import release_emu
from test_desktop_install import TempCase

CHANNEL_RS = ROOT / "desktop" / "src-tauri" / "src" / "channel.rs"
TAURI_CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"


def rust_table(name: str) -> str:
    text = CHANNEL_RS.read_text(encoding="utf-8")
    m = re.search(rf"const {name}: &\[[^\]]*\] = &\[(.*?)\n    \];", text, re.DOTALL)
    assert m, f"{name} not found in {CHANNEL_RS}"
    return m[1]


def rust_str(name: str) -> str:
    text = CHANNEL_RS.read_text(encoding="utf-8")
    m = re.search(rf'const {name}: &str =\s*"([^"]*)";', text)
    assert m, f"{name} not found in {CHANNEL_RS}"
    return m[1]


TAGS = [
    (tag, stable == "true", early == "true")
    for tag, stable, early in re.findall(
        r'\("([^"]*)", (true|false), (true|false)\)', rust_table("CASES")
    )
]
OPT_IN = [
    (setting == "true", None if env == "None" else value, want == "true")
    for setting, env, value, want in re.findall(
        r'\((true|false), (None|Some\("([^"]*)"\)), (true|false)\)',
        rust_table("OPT_IN"),
    )
]


class TheRule(unittest.TestCase):
    def test_the_tables_were_read(self) -> None:
        self.assertGreaterEqual(len(TAGS), 10)
        self.assertGreaterEqual(len(OPT_IN), 6)

    def test_python_takes_the_tags_the_app_takes(self) -> None:
        for tag, stable, early in TAGS:
            with self.subTest(tag=tag):
                self.assertEqual(channel.accepts(tag, False), stable)
                self.assertEqual(channel.accepts(tag, True), early)

    def test_the_workflow_publishes_exactly_the_tags_a_channel_can_take(self) -> None:
        for tag, stable, early in TAGS:
            with self.subTest(tag=tag):
                if not early:
                    with self.assertRaises(SystemExit):
                        ra.check_tag(tag)
                else:
                    self.assertEqual(ra.check_tag(tag), not stable, "pre-release")

    def test_the_environment_wins_when_it_is_set(self) -> None:
        for setting, env, want in OPT_IN:
            environ = {} if env is None else {channel.ENV: env}
            with self.subTest(setting=setting, env=env):
                self.assertEqual(
                    channel.opted_in({channel.SETTING: setting}, environ), want
                )
        self.assertFalse(channel.opted_in(None, {}))
        self.assertFalse(
            channel.opted_in({"prerelease": "yes"}, {}), "true, not truthy"
        )

    def test_the_manifest_urls_are_the_apps(self) -> None:
        self.assertEqual(rust_str("ENV"), channel.ENV)
        self.assertEqual(rust_str("STABLE"), channel.STABLE_LATEST_JSON)
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        self.assertEqual(
            conf["plugins"]["updater"]["endpoints"], [channel.STABLE_LATEST_JSON]
        )
        self.assertEqual(rust_str("LATEST_JSON"), channel.LATEST_JSON)
        self.assertEqual(rust_str("ROUTE"), "/radio/app/release")

    def test_versions_order_as_semver_does(self) -> None:
        ordered = [
            "v0.9.9",
            "v0.10.0-alpha",
            "v0.10.0-alpha.1",
            "v0.10.0-alpha.beta",
            "v0.10.0-beta.2",
            "v0.10.0-beta.11",
            "v0.10.0-rc.1",
            "v0.10.0",
            "v1.0.0",
        ]
        keys = [channel.version_key(t) for t in ordered]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))
        self.assertIsNone(channel.version_key("v5.73"))

    def test_is_newer_never_goes_down_or_off_the_channel(self) -> None:
        self.assertTrue(channel.is_newer("v0.10.0", "v0.9.9"))
        self.assertTrue(channel.is_newer("v1.0.0", "v0.99.99"))
        self.assertFalse(channel.is_newer("v0.1.0", "v0.1.0"))
        self.assertFalse(channel.is_newer("v0.1.0", "v0.2.0"), "never a downgrade")
        self.assertTrue(channel.is_newer("v0.1.0", ""), "a source install takes any")
        self.assertTrue(channel.is_newer("v0.1.0", "source"))
        self.assertFalse(channel.is_newer("nightly", ""))
        self.assertFalse(channel.is_newer("v0.2.0-beta", "v0.1.0"), "stable only")
        self.assertTrue(channel.is_newer("v0.2.0-beta", "v0.1.0", prerelease=True))
        self.assertTrue(channel.is_newer("v0.2.0", "v0.2.0-rc.3", prerelease=True))
        self.assertTrue(channel.is_newer("v0.2.0", "v0.2.0-rc.3"), "rc to its release")
        self.assertFalse(channel.is_newer("v0.2.0-rc.1", "v0.2.0", prerelease=True))


class Newest(unittest.TestCase):
    def setUp(self) -> None:
        self.github = release_emu.ReleaseEmu().start()
        self.addCleanup(self.github.stop)

    def publish(self, *tags: str, **kw: bool) -> None:
        for tag in tags:
            self.github.publish(tag, {f"{tag}.txt": b"x"}, **kw)

    def newest(self, prerelease: bool) -> str:
        return channel.newest(self.github.fetch, prerelease).tag

    def test_stable_asks_latest_and_the_opt_in_asks_the_list(self) -> None:
        self.publish("v1.0.0", "v1.1.0", "v1.2.0-rc.1")
        self.assertEqual(self.newest(False), "v1.1.0")
        self.assertEqual(self.newest(True), "v1.2.0-rc.1")
        self.assertEqual(
            [h.split("?")[0].rsplit("/", 1)[1] for h in self.github.hits],
            ["latest", "releases"],
            "one API call each",
        )
        self.publish("v1.2.0")
        self.assertEqual(self.newest(True), "v1.2.0", "a release beats its rc")

    def test_drafts_are_never_offered(self) -> None:
        self.publish("v1.0.0")
        self.publish("v2.0.0", draft=True)
        self.publish("v2.1.0-rc.1", draft=True)
        self.assertEqual(self.newest(True), "v1.0.0")

    def test_nothing_unreachable_or_not_json_is_a_release_error(self) -> None:
        with self.assertRaisesRegex(rel.ReleaseError, "lists no release"):
            self.newest(True)
        self.github.publish("nightly", {})
        with self.assertRaisesRegex(rel.ReleaseError, "lists no release"):
            self.newest(True)
        with self.assertRaisesRegex(rel.ReleaseError, "not JSON"):
            channel.newest(lambda _url: b"<html>", True)
        self.github.stop()
        with self.assertRaisesRegex(rel.ReleaseError, "cannot reach GitHub"):
            self.newest(True)

    def test_the_app_manifest_is_the_releases_own(self) -> None:
        self.publish("v1.2.0-rc.1")
        found = channel.newest(self.github.fetch, True)
        self.assertEqual(
            channel.app_manifest(found),
            f"https://github.com/{rel.REPO}/releases/download/v1.2.0-rc.1/latest.json",
        )


class TheInstallerAgrees(TempCase):
    """The uv installer's --update and the launcher's daily notice take the
    same opt-in, from the same settings.json the app reads."""

    def setUp(self) -> None:
        super().setUp()
        self.github = release_emu.ReleaseEmu().start()
        self.addCleanup(self.github.stop)
        for tag in ("v0.1.0", "v0.2.0-rc.1"):
            self.github.publish(tag, {})
        de.write_json(
            self.dirs.install_file, {"data": str(self.dirs.data), "tag": "v0.1.0"}
        )

    def opt_in(self) -> None:
        de.write_json(self.dirs.settings_file, {"prerelease": True})

    def notice(self, settings: dict[str, object]) -> str | None:
        stamp = self.dirs.data / dl.UPDATE_STAMP
        stamp.unlink(missing_ok=True)
        now = datetime(2026, 10, 2, tzinfo=UTC)
        return dl.check_for_update(
            self.dirs, {"tag": "v0.1.0"}, settings, self.github.fetch, now
        )

    def test_the_launcher_names_a_pre_release_only_to_an_owner_who_asked(
        self,
    ) -> None:
        self.assertIsNone(self.notice({}))
        said = self.notice({"prerelease": True})
        self.assertIsNotNone(said)
        self.assertIn("v0.2.0-rc.1 is available", str(said))

    def test_update_moves_to_a_pre_release_only_when_opted_in(self) -> None:
        said: list[str] = []
        args = self.args("--update", "--dry-run")
        life.update(args, self.dirs, self.github.fetch, lambda _c: 0, said.append)
        self.assertIn("is up to date (latest: v0.1.0)", said[-1])
        self.opt_in()
        self.assertEqual(
            life.update(args, self.dirs, self.github.fetch, lambda _c: 0, said.append),
            0,
        )
        self.assertIn("updating Castle Tools v0.1.0 -> v0.2.0-rc.1", said)

    def test_the_launch_hands_the_opt_in_to_every_child(self) -> None:
        env = de.launch_env(self.dirs, {}, {"prerelease": True}, {})
        self.assertEqual(env.get(channel.ENV), "1")
        self.assertNotIn(channel.ENV, de.launch_env(self.dirs, {}, {}, {}))
        self.assertNotIn(
            channel.ENV,
            de.launch_env(self.dirs, {}, {"prerelease": "true"}, {}),
            "only a real true",
        )
        said_no = de.launch_env(self.dirs, {}, {"prerelease": True}, {channel.ENV: "0"})
        self.assertEqual(said_no.get(channel.ENV), "0", "the environment wins")


if __name__ == "__main__":
    unittest.main()
