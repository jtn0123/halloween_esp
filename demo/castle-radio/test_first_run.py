"""First run — GET /radio/first-run (first_run.py) and where its script sits.

An installed app has no media/ (it is not in git), so the route must say
which demo files THIS computer has — names only — and first-run.js must run
after the scripts whose track list and renderer it leans on.
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import first_run
import server
from test_server_fixes import _Caller

HERE = Path(__file__).resolve().parent


def ask():
    caller = _Caller(path="/radio/first-run")
    caller.do_GET()
    return caller.answer()


class FirstRunRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="radio-first-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_an_installed_app_has_no_demo_and_says_so(self):
        with mock.patch.object(first_run, "MEDIA", self.tmp / "media"):
            self.assertEqual(ask(), (200, {"demo": []}))

    def test_only_the_demo_mp3s_present_are_named_and_never_a_path(self):
        media = self.tmp / "media"
        (media / "folder.mp3").mkdir(parents=True)
        for name in ("02_storm.mp3", "01_vigil.mp3", "notes.txt"):
            (media / name).write_bytes(b"x")
        with mock.patch.object(first_run, "MEDIA", media):
            self.assertEqual(ask(), (200, {"demo": ["01_vigil.mp3", "02_storm.mp3"]}))

    def test_the_route_is_served_and_the_script_runs_after_what_it_wraps(self):
        self.assertIn("/radio/first-run", server.Handler.GET_ROUTES)
        self.assertIn("/first-run.js", server.STATIC_ROUTES)
        page = (HERE / "index.html").read_text(encoding="utf-8")
        order = re.findall(r'<script src="([^"]+)"></script>', page)
        for before in ("app.js", "imports.js"):
            self.assertLess(order.index(before), order.index("first-run.js"))
        # What it compares: every demo row app.js lists names an mp3 file.
        app = (HERE / "app.js").read_text(encoding="utf-8")
        files = re.findall(r"'(\d\d_[^']+)'", app)
        self.assertEqual(len(files), 10)
        self.assertTrue(all(f.endswith(".mp3") for f in files), files)


if __name__ == "__main__":
    unittest.main()
