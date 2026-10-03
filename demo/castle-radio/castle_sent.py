"""What this Castle Radio last put on which castle — so a sync that stopped
partway skips, when it is run again, what already landed.

The castle has no way to say what a file on its card IS short of sending it
back, but it does say, in answer to every upload, the CRC of what it wrote
(firmware/sd_web_upload.h). That answer is filed here per host — two castles
do not share a card — as card path -> [bytes, crc32]. A file is "already
there and identical" when the card's listing has it at the same size AND the
CRC the castle reported for it is the CRC of the bytes about to go; anything
else is sent. Losing the file costs a re-send, never a wrong card.

It lives in the radio's data dir (CASTLE_RADIO_DATA, radio_env.DATA) beside
the catalog, so the desktop app keeps it per user and a checkout keeps it in
its own .radio-data/.
"""

import json
import threading
import zlib

import radio_env  # the sandbox first, then tools/ on the path

# isort: split
import portable_fs

FILE = radio_env.DATA / "castle-sent.json"
_LOCK = threading.Lock()


def crc(data):
    return f"{zlib.crc32(data):08x}"


def _load():
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def landed(host, path, data, listed_size):
    """Is `path` on `host`'s card exactly these bytes, by the castle's word?"""
    if listed_size != len(data):
        return False
    with _LOCK:
        mine = _load().get(host)
    return isinstance(mine, dict) and mine.get(path) == [len(data), crc(data)]


def record(host, path, data, reported):
    """File the castle's own CRC for `path`, when it gave one and it is the
    CRC of `data` — an older castle that reports none proves nothing here.
    Best effort: a record that cannot be written is a slower retry."""
    if reported is None or int(str(reported), 16) != zlib.crc32(data):
        return
    with _LOCK:
        hosts = _load()
        mine = hosts.get(host) if isinstance(hosts.get(host), dict) else {}
        hosts[host] = {**mine, path: [len(data), crc(data)]}
        temp = FILE.with_suffix(".tmp")
        try:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(hosts, indent=1, sort_keys=True), encoding="utf-8"
            )
            portable_fs.replace(temp, FILE)
        except OSError:
            temp.unlink(missing_ok=True)
