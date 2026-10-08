"""What an owner reads in Castle Radio says nothing of a demo, or of a Mac.

Castle Radio runs inside the desktop app, Castle Tools, for a castle's owner,
who has no demo — and Castle Tools runs on Windows as well as on a Mac. These
are index.html's lines that used to say otherwise, each pinned in the owner's
words — on the computer's page, and on the castle's, which device_site.py
builds from the same file. The undo bar's sentence and the castle's
inventory card are written by scripts, and are run where they are written
(test_owner_words.test.mjs); the import queue's "Ready" is pinned where a
job finishes (test_radio_catalog.py, test_radio_cancel.py). The scripts'
own sentences about the tools are pinned in SCRIPT_WORDS below, and the ones
that run cheaply are run in test_owner_words.test.mjs too.
"""

import re
import shutil
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import device_site

HERE = Path(__file__).resolve().parent

#: (the demo's words, the owner's), as index.html carries them.
WORDS = (
    (
        "Your existing tracks are loaded for this demo.",
        "Paste a link or choose a file in Import music, and the song joins your collection.",
    ),
    (
        "Production will analyze the song and prepare synchronized cues.",
        "Each song is analyzed on your computer, and its light cues are prepared to match.",
    ),
    (
        "The song and light show will be stored together on the castle.",
        # The page's own apostrophe is U+2019, escaped here for ruff's sake.
        "Sync stores the song and its light show together on the castle\u2019s SD card.",
    ),
    (
        (
            "These choices update this demo immediately and save on this browser. "
            "Changing the physical fixture or pixel count still requires generating "
            "and deploying matching firmware."
        ),
        (
            "These choices change the live preview straight away and are saved in "
            "this browser. They do not change the castle: different fixtures or LED "
            "counts on the castle need firmware made for them."
        ),
    ),
    ("<dt>Demo collection</dt>", "<dt>Installed shows</dt>"),
    ("Song removed from this demo.", "Song removed from your collection."),
    (
        "The existing importer downloads or converts your audio.",
        "Your song is downloaded from its link, or converted from the file you chose.",
    ),
    (
        "The existing voice/background splitter separates and analyzes both parts.",
        "The singing is separated from the music behind it, and both parts are analyzed.",
    ),
)

#: (script, the Mac's words, the owner's): the sentences the castle page's
#: scripts write about the tools, which a Windows owner reads too. Readable
#: skips scripts, so these are pinned in the file that says each one.
SCRIPT_WORDS = (
    ("desktop-tools.js", "'Connect your Mac'", "'Connect your computer'"),
    ("desktop-tools.js", "'Connect Mac tools'", "'Connect Castle Tools'"),
    ("desktop-tools.js", "'Reconnect Mac tools'", "'Reconnect Castle Tools'"),
    ("desktop-tools.js", "'Starting Mac tools…'", "'Starting Castle Tools…'"),
    ("desktop-tools.js", "'Connecting to your Mac…'", "'Connecting to your computer…'"),
    (
        "desktop-tools.js",
        "'Click Start Mac tools, then Connect Mac tools.",
        "'Click Start Castle Tools, then Connect Castle Tools.",
    ),
    (
        "desktop-tools.js",
        "enable this once on your Mac.'",
        "enable this once on your computer.'",
    ),
    (
        "device-helper.js",
        "'Imports and voice separation run on your Mac.'",
        "'Imports and voice separation run on your computer.'",
    ),
    (
        "device-helper.js",
        "'Connect Mac tools to import songs here.'",
        "'Connect Castle Tools to import songs here.'",
    ),
    (
        "device-helper.js",
        "'Mac tools disconnected. Keep the connection window open.'",
        "'Castle Tools disconnected. Keep the connection window open.'",
    ),
    ("device-helper.js", "'Connect Mac tools first.'", "'Connect Castle Tools first.'"),
    (
        "device-helper.js",
        "'Mac tools did not answer. Check the connection window.'",
        "'Castle Tools did not answer. Check the connection window.'",
    ),
    (
        "device-helper.js",
        "'Mac tools did not answer in time.'",
        "'Castle Tools did not answer in time.'",
    ),
    (
        "app.js",
        "'Mac audio unavailable. Reconnect Mac tools and select the song again.'",
        (
            "'This song could not be loaded from your computer. "
            "Reconnect Castle Tools and select the song again.'"
        ),
    ),
    (
        "imports.js",
        "'Restart Castle Radio on your Mac to cancel imports",
        "'Restart Castle Tools on your computer to cancel imports",
    ),
    (
        "imports.js",
        "'Restart Castle Radio on your Mac to rename songs",
        "'Restart Castle Tools on your computer to rename songs",
    ),
    (
        "companion.js",
        "'Open this helper from Castle Studio on this Mac.'",
        "'Open this window from the castle page, with Connect Castle Tools.'",
    ),
)

#: What device_site.py rewrites into the castle's page alone.
CASTLE_ONLY = (
    "'Connect Castle Tools to view waveforms, then reopen this song.'",
    "'Castle Tools connected · imports are prepared on your computer'",
    "'Castle library ready · connect Castle Tools to import'",
)

#: The attributes a person reads or hears, besides the text itself.
READ_ALOUD = ("aria-label", "title", "placeholder", "alt")


class Readable(HTMLParser):
    """Every piece of text a page shows or says: text outside <script> and
    <style>, and the attributes above. The castle's page inlines its scripts,
    whose comments are for whoever reads the source."""

    def __init__(self):
        super().__init__()
        self.said: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden_depth += 1
        self.said += [value for name, value in attrs if name in READ_ALOUD and value]

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth and data.strip():
            self.said.append(data.strip())


def readable(page: str) -> list[str]:
    parser = Readable()
    parser.feed(page)
    return parser.said


class OwnerWords(unittest.TestCase):
    def setUp(self):
        data = Path(tempfile.mkdtemp(prefix="castle-radio-words-"))
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        self.pages = {
            "computer": (HERE / "index.html").read_text(encoding="utf-8"),
            "castle": device_site.build(HERE, data).decode(),
        }

    def test_each_line_is_in_the_owners_words_on_both_pages(self):
        for name, page in self.pages.items():
            for old, new in WORDS:
                with self.subTest(page=name, line=new):
                    self.assertNotIn(old, page)
                    self.assertEqual(page.count(new), 1)

    def test_no_text_an_owner_sees_or_hears_says_demo(self):
        for name, page in self.pages.items():
            with self.subTest(page=name):
                said = readable(page)
                self.assertIn("Installed shows", said)
                self.assertEqual([s for s in said if "demo" in s.lower()], [])

    def test_no_text_an_owner_sees_or_hears_names_a_mac(self):
        """The tools card's buttons, its setup steps and the connection
        window all name Castle Tools, which a Windows owner runs too."""
        companion = (HERE / "companion.html").read_text(encoding="utf-8")
        for name, page in dict(self.pages, companion=companion).items():
            with self.subTest(page=name):
                said = readable(page)
                self.assertEqual([s for s in said if re.search(r"\bMac\b", s)], [])
        for name in ("computer", "castle"):
            said = readable(self.pages[name])
            self.assertIn("Start Castle Tools", said)
            self.assertIn("Connect Castle Tools", said)
        self.assertIn("Castle Tools connection", readable(companion))

    def test_the_scripts_say_castle_tools_and_your_computer(self):
        for script, old, new in SCRIPT_WORDS:
            with self.subTest(script=script, line=new):
                source = (HERE / script).read_text(encoding="utf-8")
                self.assertNotIn(old, source)
                self.assertEqual(source.count(new), 1)

    def test_the_castle_pages_own_lines_name_castle_tools(self):
        page = self.pages["castle"]
        for line in CASTLE_ONLY:
            with self.subTest(line=line):
                self.assertEqual(page.count(line), 1)
        self.assertNotIn("Mac tools", page)

    def test_the_reader_skips_scripts_and_keeps_labels(self):
        said = readable(
            "<p>One</p><script>// a demo comment</script><style>.demo{}</style>"
            '<button aria-label="Two" title="Three">Four</button>'
            '<input placeholder="Five"><img alt="Six">'
        )
        self.assertEqual(said, ["One", "Two", "Three", "Four", "Five", "Six"])


if __name__ == "__main__":
    unittest.main()
