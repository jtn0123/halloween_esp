"""What an owner reads in Castle Radio says nothing of a demo.

Castle Radio runs inside the desktop app, Castle Tools, for a castle's owner,
who has no demo. These are index.html's lines that used to say so, each
pinned in the owner's words — on the computer's page, and on the castle's,
which device_site.py builds from the same file. The undo bar's sentence and
the castle's inventory card are written by scripts, and are run where they
are written (test_owner_words.test.mjs); the import queue's "Ready" is
pinned where a job finishes (test_radio_catalog.py, test_radio_cancel.py).
"""

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

    def test_the_reader_skips_scripts_and_keeps_labels(self):
        said = readable(
            "<p>One</p><script>// a demo comment</script><style>.demo{}</style>"
            '<button aria-label="Two" title="Three">Four</button>'
            '<input placeholder="Five"><img alt="Six">'
        )
        self.assertEqual(said, ["One", "Two", "Three", "Four", "Five", "Six"])


if __name__ == "__main__":
    unittest.main()
