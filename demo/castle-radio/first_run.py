"""First run — what Castle Radio really has to play on this computer.

    GET /radio/first-run   {"demo": [the demo files present in media/]}

The ten demo tracks app.js lists are the castle's own scene renders, copied
into media/ by hand on the machine this demo was built on. media/ is not in
git (.gitignore), so an installed app — a release, the desktop bundle — has
none of them, and listing them anyway gave a new owner ten rows that each
said "This audio file is unavailable". first-run.js hides the rows whose file
is not here and, when nothing is left, shows the first-run card: add your
first song. Names only, never a path.
"""

from pathlib import Path

MEDIA = Path(__file__).resolve().parent / "media"


def demo_files():
    try:
        return sorted(p.name for p in MEDIA.glob("*.mp3") if p.is_file())
    except OSError:
        return []


def get_first_run(handler, _parsed):
    handler.reply({"demo": demo_files()})


GET_ROUTES = {"/radio/first-run": get_first_run}
