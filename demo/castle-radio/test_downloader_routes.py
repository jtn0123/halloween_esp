"""Update the downloader, from Castle Radio's button (downloader_routes.py).

No network: tools/test_ytdlp_update.py proves the fetch and the checksum
against a fake GitHub; these hold what the radio adds — the update queues
behind the imports and never runs in the middle of one, says how it went in
the owner's words, and the tool checks forget the copy they found.
"""

import concurrent.futures
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# The sandbox first, then tools/ on the path.
import radio_env  # noqa: F401

# isort: split
import castle_tools_status
import downloader_routes as dr
import import_reason as ir
import radio_jobs
import ytdlp_update as yu


def press():
    handler = MagicMock()
    dr.post_update(handler)
    status, code = handler.reply.call_args.args
    handler.json_body.assert_called_once()  # a JSON route, like every POST
    return status["update"], code


class RouteCase(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        env = {"CASTLE_DOWNLOADER_DIR": self.home, "CASTLE_YTDLP": ""}
        self.enterContext(patch.dict(os.environ, env))
        self.enterContext(patch.dict(dr.STATE, {"phase": "idle"}, clear=True))
        self.enterContext(patch.dict(radio_jobs.JOBS, {}, clear=True))
        self.cleared = self.enterContext(
            patch.object(castle_tools_status.status, "cache_clear")
        )


class QueueTests(RouteCase):
    def test_the_update_waits_for_the_import_ahead_of_it(self):
        pool = self.enterContext(concurrent.futures.ThreadPoolExecutor(max_workers=1))
        self.enterContext(patch.object(radio_jobs, "POOL", pool))
        importing, release = threading.Event(), threading.Event()

        def an_import():
            importing.set()
            release.wait(10)
            calls.append("import done")

        calls = []
        radio_jobs.JOBS["radio_i"] = {"id": "radio_i", "done": False}
        pool.submit(an_import)
        importing.wait(10)

        def fake_update(home, **_):
            calls.append("update")
            return {"changed": True, "version": "2026.10.01", "path": "x"}

        with patch.object(yu, "update", side_effect=fake_update):
            state, code = press()
            self.assertEqual((state["phase"], code), ("waiting", 202))
            # A second press while it waits is the same update, not another.
            self.assertEqual(press()[0]["phase"], "waiting")
            self.assertEqual(calls, [])
            release.set()
            pool.shutdown(wait=True)
        self.assertEqual(calls, ["import done", "update"])
        now = dr.current()
        self.assertEqual(
            (now["phase"], now["version"], now["changed"]),
            ("done", "2026.10.01", True),
        )
        self.cleared.assert_called_once()

    def test_with_nothing_queued_it_starts_at_once(self):
        pool = self.enterContext(patch.object(radio_jobs, "POOL"))
        self.assertEqual(press()[0]["phase"], "updating")
        pool.submit.assert_called_once_with(dr.run_update)


class OutcomeTests(RouteCase):
    def test_a_failure_is_the_owners_sentence_with_the_detail_kept(self):
        refused = yu.UpdateError(yu.TAMPERED, "checksum mismatch for yt-dlp_macos")
        with patch.object(yu, "update", side_effect=refused):
            dr.run_update()
        now = dr.current()
        self.assertEqual((now["phase"], now["error"]), ("failed", yu.TAMPERED))
        self.assertIn("checksum mismatch", now["error_detail"])
        self.cleared.assert_called_once()

    def test_an_unexpected_crash_still_ends_the_wait(self):
        with patch.object(yu, "update", side_effect=KeyError("assets")):
            dr.run_update()
        now = dr.current()
        self.assertEqual((now["phase"], now["error"]), ("failed", ir.GENERIC))
        self.assertIn("KeyError", now["error_detail"])

    def test_no_folder_to_keep_it_in(self):
        with (
            patch.dict(os.environ, {"CASTLE_DOWNLOADER_DIR": ""}),
            patch.object(yu, "update") as update,
        ):
            dr.run_update()
        update.assert_not_called()
        self.assertEqual(dr.current()["error"], yu.NO_HOME)

    def test_the_update_lands_in_the_radios_downloader_folder(self):
        got = {"changed": False, "version": "2026.10.01", "path": "x"}
        with patch.object(yu, "update", return_value=got) as update:
            dr.run_update()
        self.assertEqual(str(update.call_args.args[0]), self.home)


class StatusTests(RouteCase):
    def test_the_state_names_what_an_import_would_run(self):
        handler = MagicMock()
        with (
            patch.object(yu, "run_version", return_value="2026.09.01") as asked,
            patch("exe_paths.ytdlp", return_value="/opt/yt-dlp"),
        ):
            dr.get_status(handler, None)
            dr.get_status(handler, None)
        body = handler.reply.call_args.args[0]
        self.assertEqual(
            (body["installed"], body["managed"], body["version"]),
            (True, False, "2026.09.01"),
        )
        self.assertEqual(body["update"], {"phase": "idle"})
        # A path that cannot be read is asked each time, never remembered.
        self.assertEqual(asked.call_count, 2)

    def test_a_version_is_remembered_until_the_file_changes(self):
        program = os.path.join(self.home, "yt-dlp")
        with open(program, "w", encoding="utf-8") as f:
            f.write("one")
        with patch.object(yu, "run_version", return_value="1") as asked:
            dr.version(program)
            dr.version(program)
            self.assertEqual(asked.call_count, 1)
            stat = os.stat(program)
            os.utime(program, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
            dr.version(program)
            self.assertEqual(asked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
