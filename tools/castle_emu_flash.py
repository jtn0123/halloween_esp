"""What the castle serves out of FLASH rather than off the card, and the
constants that shape it: the two pages compiled into the image (lifted out
of their C raw strings so a placeholder can never drift from the board),
the MIME table, the CSP header and the boot-log stub.

Split out of castle_emu_http.py on that seam when the host C harness
(tests/cxx/web_check.cpp) started comparing every served body byte for
byte and the handlers file reached the 500-line cap. Nothing here answers
a request; castle_emu_http.py's handlers import what they send.
"""

from __future__ import annotations

import re
from pathlib import Path

#: /api/bootlog's stand-in ring. The board dumps its own; what has to be
#: right is the SHAPE — h_bootlog prints `held` in the header and then
#: exactly `held` lines, so a stub claiming two and printing one (as this
#: did until the C harness read both bodies) is a boot log no firmware can
#: produce. One line, one count.
BOOTLOG = b"boot log: 1 lines, 0 dropped\n[I][emu] up\n"

#: sd_web_site.h content_type(): suffix → MIME.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",  # castle_emu_http.JSON_MIME, spelled here
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

#: Where the two flash pages live.
_FW = Path(__file__).resolve().parent.parent / "firmware"


def flash_page(header: str, symbol: str) -> str:
    """One of the pages the firmware carries in flash, lifted out of its C
    raw string.

    The phone remote came first (JB2-6): it is embedded in the image, so a
    placeholder here would be a page nobody could test, and the e2e suite
    drives whatever the C says. The fallback page followed it once the C
    harness started comparing `/`'s body byte for byte."""
    src = (_FW / header).read_text()
    m = re.search(rf'{symbol}\[\] = R"HTML\((.*?)\)HTML";', src, re.DOTALL)
    if not m:
        raise RuntimeError(f"no {symbol} raw string in {header}")
    return m.group(1)


REMOTE_PAGE = flash_page("sd_web_remote.h", "kRemotePage")
#: h_root's answer when the card has no /site/index.html — or no card.
FALLBACK_PAGE = flash_page("sd_web_site.h", "kFallbackPage")
#: sd_web_site.h set_csp(), byte for byte (E4) — sent on every served page.
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "media-src 'self' data: blob:; connect-src 'self'"
)
