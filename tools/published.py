"""What this Mac last PUT to which castle — so a publish can skip a GET.

`sd_sync scenes` runs after every scene save, and the card's listing only
tells it a name and a size. A size match used to be confirmed by pulling the
whole file back over Wi-Fi (`GET /sd/scenes/<name>`), which is 8 MB of
unchanged audio to decide nothing changed (grade report 2026-09-17 pm G1).

The record below is the cheap half of that check: name -> sha256 of the bytes
we last successfully uploaded, kept PER HOST because two castles do not share
a card. A size match plus a hash match means skip; anything else — a missing
record, a hash that differs, a file written by another tool — falls back to
the byte compare and then records what it learned, so the record is an
accelerator and never the only evidence.

It lives under the build root (`audio/card/.published.json`, gitignored with
the rest of audio/card/), so a sandboxed studio records beside its own
renders and CASTLE_BUILD moves it without a thought. Losing it costs one
slow publish.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import build_paths as bp


def record_path() -> Path:
    return bp.AUDIO / "card" / ".published.json"


class Published:
    """The record for ONE host, loaded on construction, saved on `save()`."""

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.hosts = _load(record_path())
        self.mine: dict[str, str] = dict(self.hosts.get(ip) or {})
        self.dirty = False

    def matches(self, key: str, data: bytes) -> bool:
        """True when `key` on this host already holds exactly these bytes."""
        return self.mine.get(key) == _sha(data)

    def record(self, key: str, data: bytes) -> None:
        digest = _sha(data)
        if self.mine.get(key) != digest:
            self.mine[key] = digest
            self.dirty = True

    def forget(self, key: str) -> None:
        if self.mine.pop(key, None) is not None:
            self.dirty = True

    def save(self) -> None:
        """Best effort: a record we cannot write is a slow publish, not a
        failed one, so a read-only build tree must not stop the push."""
        if not self.dirty:
            return
        self.hosts[self.ip] = self.mine
        path = record_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.hosts, indent=1, sort_keys=True) + "\n")
        except OSError as e:
            print(f"  (could not write {bp.rel(path)}: {e})")
            return
        self.dirty = False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load(path: Path) -> dict[str, dict[str, str]]:
    """host -> {name: sha256}. Anything unreadable or the wrong shape is
    simply no record: the byte compare is still there to be right."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        host: {
            n: h for n, h in names.items() if isinstance(n, str) and isinstance(h, str)
        }
        for host, names in raw.items()
        if isinstance(host, str) and isinstance(names, dict)
    }
