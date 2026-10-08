"""`sd_sync.py logs` — the castle's own log, fetched off the card.

Split out of tools/sd_sync.py at the 500-line cap, on the seam the command
list draws: this READS what the castle wrote, where the rest of that tool
writes the card. It is handed `api` and the repo root, as tools/sd_ota.py
is, for the same reason — sd_sync is a script as well as a module, so a
back-import would be a cycle.

L9 (v5.62): the card's own log, oldest rotation first. The castle has
written one line per boot since v5.44 and, since v5.62, the tail of the
previous life's event ring underneath it — and nothing ever fetched it, so
the one record that survives a crash was only readable by pulling the card.
It has been HTTP-readable the whole time (sd_web_site.h).
"""

from __future__ import annotations

from pathlib import Path

from sd_ota import Api

LOG_FILES = ("logs/castle.log.1", "logs/castle.log")
#: Lines printed after the save. The whole file goes to disk; this is the
#: part you read standing in the hall with a laptop.
TAIL_LINES = 40


def fetch(ip: str, args: list[str], api: Api, root: Path) -> int:
    out = Path(args[0]) if args else root / "castle.log"
    text = ""
    for name in LOG_FILES:
        # A constant path, not one built from anything the castle said: the
        # rule for every URL sd_sync sends.
        try:
            text += api(ip, "GET", f"/sd/{name}").decode("utf-8", "replace")
        except OSError as e:
            # castle.log.1 only exists after the first rotation (~200 KB),
            # so its absence is the normal case and not a failure.
            print(f"  {name}: {e}")
    if not text.strip():
        print("no log on the card — has this castle booted with it in the slot?")
        return 1
    out.write_text(text, encoding="utf-8")
    lines = text.splitlines()
    print(f"saved {len(lines)} lines to {out}\n")
    print("\n".join(lines[-TAIL_LINES:]))
    return 0
