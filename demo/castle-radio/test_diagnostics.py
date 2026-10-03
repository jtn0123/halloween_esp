"""Copy diagnostics (diagnostics.py) against the emulated castle.

Held to: the text is the castle's own problem report (firmware/sd_web_owner.h,
v5.75) — its header, its four sections in its order — with this computer's
half after it; no castle key survives anywhere in it, raw, quoted, in a
header or a query; no folder on this computer survives either, while castle
routes and card paths do; a castle that is not there costs no socket and a
silent one costs one timeout, not four; and the route answers with the text
and a file name. Every card, store and log is a temp file; the castle is
127.0.0.1 on port 0.
"""

import os
import re
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import radio_env  # the sandbox first, then tools/ on the path

# isort: split
import device_bridge
import diagnostics
import radio_jobs
import remote_library
from castle_emu import CastleEmu

KEY = "Gr4veyard-Key/9"
PINNED_KEY = "pinned+key"
HOME = str(Path.home())
TOOLS = {
    "checks": [
        {"name": "ffmpeg", "ok": True, "detail": "/opt/homebrew/bin/ffmpeg 7.1"},
        {"name": "yt-dlp", "ok": False, "detail": "not found"},
    ]
}
NOW = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="radio-diag-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.emu = CastleEmu(port=0, sd_dir=self.tmp / "card", fw_variant="buyer")
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.host = f"127.0.0.1:{self.emu.port}"
        store = self.tmp / "devices.toml"
        store.write_text(
            f'[castle]\nhost = "{self.host}"\nkey = "{KEY}"\n', encoding="utf-8"
        )
        self.log = self.tmp / "Castle Logs" / "castle.log"
        self.log.parent.mkdir()
        self.log.write_text(
            "\n".join(
                [
                    "old line that falls off the tail",
                    *[f"line {n}" for n in range(diagnostics.LOG_LINES - 5)],
                    f"starting radio from {HOME}/Library/Application Support/x",
                    f"relay POST /api/key?new={KEY.replace('/', '%2F')} -> 200",
                    f"headers {{'X-Castle-Key': '{PINNED_KEY}'}}",
                    f"error reading {self.tmp}/tracks/My Song.mp3: gone",
                    "C:\\Users\\someone\\AppData\\Castle\\radio.log rotated",
                ]
            ),
            encoding="utf-8",
        )
        env = {
            "CASTLE_DEVICES": str(store),
            "CASTLE_KEY": PINNED_KEY,
            "CASTLE_APP_VERSION": "0.9.1",
            "CASTLE_APP_LOG": str(self.log),
        }
        for patch in (
            mock.patch.dict(os.environ, env),
            mock.patch.object(device_bridge, "HOST", self.host),
            mock.patch.object(
                diagnostics.castle_tools_status, "status", return_value=TOOLS
            ),
            mock.patch.dict(
                radio_jobs.JOBS,
                {
                    "t1": {
                        "id": "t1",
                        "title": "",
                        "source": f"file:{HOME}/Music/Spooky Song.mp3",
                        "phase": "Failed",
                        "done": True,
                        "error": f"ffmpeg could not read {HOME}/Music/x.wav",
                    }
                },
            ),
            mock.patch.dict(
                remote_library._JOBS,
                {"radio_a": {"key": "radio_a", "phase": "Synced", "percent": 100}},
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_report_is_the_castles_own_with_this_computers_half_after_it(self):
        text = diagnostics.report(NOW)
        lines = text.splitlines()
        self.assertEqual(lines[0], "Castle problem report")
        self.assertTrue(lines[1].startswith("made 2026-10-02T10:00:00+00:00 ("))
        self.assertEqual(
            lines[2],
            f"version {self.emu.version} · board {self.emu.board} · build buyer",
        )
        self.assertEqual(lines[3], "page Castle 0.9.1 diagnostics")
        heads = re.findall(r"^== (.+) ==$", text, re.MULTILINE)
        self.assertEqual(
            heads,
            [
                *diagnostics.CASTLE_PATHS,
                "Castle Radio",
                "tools",
                f"recent jobs (last {diagnostics.JOB_LIMIT} of each)",
                f"app log (last {diagnostics.LOG_LINES} lines)",
            ],
        )
        self.assertIn('"fitted":false', text, "the castle's status, verbatim")
        self.assertRegex(
            text,
            rf"castle {re.escape(self.host)} · firmware {re.escape(self.emu.version)}"
            r" · last restart \S+ · switched on \d+ times, \d+ crashes",
        )
        self.assertIn("ffmpeg: ok · ffmpeg 7.1", text)
        self.assertIn("import t1 · file:Spooky Song.mp3 · Failed · ffmpeg could", text)
        self.assertIn("sync radio_a · Synced · 100%", text)
        self.assertNotIn("old line that falls off the tail", text)
        self.assertIn("line 0\n", text)

    def test_no_key_and_no_folder_survive_and_castle_paths_do(self):
        text = diagnostics.report(NOW)
        for secret in (KEY, PINNED_KEY, "Gr4veyard-Key%2F9", "Gr4veyard"):
            self.assertNotIn(secret, text)
        self.assertIn(f"/api/key?new={diagnostics.REMOVED}", text)
        self.assertIn(f"'X-Castle-Key': '{diagnostics.REMOVED}'", text)
        for folder in (
            HOME,
            str(self.tmp),
            "Application Support",
            "AppData",
            "someone\\",
        ):
            self.assertNotIn(folder, text)
        self.assertIn("error reading My Song.mp3: gone", text)
        self.assertIn("could not read x.wav", text)
        self.assertIn("radio.log rotated", text)
        self.assertIn("starting radio from x\n", text)
        self.assertIn("ffmpeg: ok · ffmpeg 7.1", text, "/opt/homebrew/bin went")

    def test_scrub_keeps_what_is_not_a_folder(self):
        self.assertEqual(
            diagnostics.scrub(
                "GET /api/status · /radio/device/audio/a.mp3 · /sd/scenes/show.man"
                " · http://192.168.1.4/owner · ~/Music/a.mp3 · /usr/bin/python3"
                " · C:\\Users\\someone\\x.log · see /etc/hosts"
                " · /Users/someone/Music Box/b.mp3 · /usr/bin/ffmpeg then 3/4 done"
            ),
            "GET /api/status · /radio/device/audio/a.mp3 · /sd/scenes/show.man"
            " · http://192.168.1.4/owner · a.mp3 · python3 · x.log · see hosts"
            " · b.mp3 · ffmpeg then 3/4 done",
        )

    def test_no_castle_costs_no_socket_and_a_silent_one_costs_one_try(self):
        with (
            mock.patch.object(device_bridge, "HOST", ""),
            mock.patch.dict(os.environ, {"CASTLE_DEVICES": ""}),
            mock.patch.object(diagnostics, "_ask") as ask,
        ):
            text = diagnostics.report(NOW)
        ask.assert_not_called()
        self.assertEqual(text.count(diagnostics.NO_ANSWER), 4)
        self.assertIn("version ? · board ? · build ?", text)
        self.assertIn("castle No castle found yet", text)
        self.emu.shutdown()
        self.emu.server_close()
        with mock.patch.object(diagnostics, "_ask", wraps=diagnostics._ask) as ask:
            text = diagnostics.report(NOW)
        self.assertEqual([c.args[1] for c in ask.call_args_list], ["/api/status"])
        self.assertIn(f"castle {self.host} · not answering", text)

    def test_a_refusal_is_quoted_as_the_owner_page_quotes_it(self):
        self.assertEqual(
            diagnostics._ask(self.host, "/api/nothing-here")[:9], "HTTP 404:"
        )

    def test_the_app_is_named_by_the_app_else_the_release_else_a_checkout(self):
        self.assertEqual(diagnostics.app_version(), "Castle 0.9.1")
        with mock.patch.dict(os.environ, {"CASTLE_APP_VERSION": ""}):
            stamped = (radio_env.ROOT / "installer" / "VERSION").read_text(
                encoding="utf-8"
            )
            if stamped.startswith("$Format"):
                self.assertIn("a checkout", diagnostics.app_version())
            with mock.patch.object(diagnostics.radio_env, "ROOT", self.tmp):
                (self.tmp / "installer").mkdir()
                (self.tmp / "installer" / "VERSION").write_text(
                    "v1.2.3\n", encoding="utf-8"
                )
                self.assertEqual(diagnostics.app_version(), "Castle Radio v1.2.3")

    def test_the_route_answers_with_the_text_and_a_file_name(self):
        handler = mock.Mock()
        diagnostics.GET_ROUTES["/radio/diagnostics"](handler, None)
        body = handler.reply.call_args.args[0]
        self.assertTrue(body["text"].startswith("Castle problem report\n"))
        self.assertRegex(
            body["name"], r"^castle-report-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d\.txt$"
        )
        self.assertNotIn(KEY, body["text"])


if __name__ == "__main__":
    unittest.main()
