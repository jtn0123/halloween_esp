"""The firmware, parsed — the layer both firmware contract suites read.

tests/test_firmware_contract.py holds the emulator to firmware/sd_web.h by
PARSING the C at test time rather than copying it, and
tests/test_firmware_names.py does the same for the helper layer next door.
The parsing itself is the same work for both: read the headers, cut them
into functions, pull the reg() table and the reply_err() strings out, and
fail loudly when a regex stops matching instead of quietly asserting
nothing.

Split out when the names suite left (grade report 2026-09-06 J1 grew it
past the 500-line rule) — on the seam the firmware already has: sd_web.h is
the routes and the handlers, sd_web_util.h is the byte rules underneath
them. Nothing here is a test; nothing here is hand-copied from the C.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

FW = ROOT / "firmware"
SD_WEB = (FW / "sd_web.h").read_text()
SD_OTA = (FW / "sd_web_ota.h").read_text()
SD_SITE = (FW / "sd_web_site.h").read_text()
SD_REMOTE = (FW / "sd_web_remote.h").read_text()
SD_STATE = (FW / "sd_web_state.h").read_text()
SD_UTIL = (FW / "sd_web_util.h").read_text()
SD_STREAM = (FW / "sd_web_stream.h").read_text()
EMU_HTTP = (ROOT / "tools" / "castle_emu_http.py").read_text()

#: reply_err strings the emulator has no way to produce: flash, heap and
#: FAT failures of the real board. Everything else must be mirrored.
HARDWARE_ONLY = {
    "no memory",
    "no OTA slot",
    "ota begin failed",
    "ota end failed",
    "could not select slot",
}


def c_functions(*sources: str) -> dict[str, str]:
    """name → body for every `inline <type> name(` at column 0."""
    out: dict[str, str] = {}
    pat = re.compile(r"^inline [\w:]+(?: \*)? ?(\w+)\(", re.MULTILINE)
    for src in sources:
        hits = list(pat.finditer(src))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(src)
            out[m.group(1)] = src[m.start() : end]
    return out


def reply_errs(body: str) -> set[tuple[int, str]]:
    return {
        (int(c), msg)
        for c, msg in re.findall(r'reply_err\(req, "(\d{3}) [^"]*", "([^"]*)"\)', body)
    }


#: Module-level string constants in the emulator, so a message spelled once
#: and reused (NO_SD) reads the same to this contract as a bare literal.
EMU_CONSTS: dict[str, str] = dict(
    re.findall(r'^([A-Z][A-Z0-9_]*) = "([^"]*)"', EMU_HTTP, re.MULTILINE)
)


def emu_errs(handler: str) -> set[tuple[int, str]]:
    """Every self._err(code, msg) inside one emulator handler method, with a
    named constant resolved to the string it holds."""
    m = re.search(rf"    def {handler}\(self.*?(?=\n    def |\Z)", EMU_HTTP, re.DOTALL)
    assert m, f"emulator has no {handler}"
    out: set[tuple[int, str]] = set()
    # The message is the second argument; a third (headers the handler had
    # already set, as h_site's CSP) may follow it, so the match stops at
    # the string rather than at the closing paren.
    for code, msg in re.findall(r'self\._err\(\s*(\d{3}),\s*"([^"]*)"', m.group(0)):
        out.add((int(code), msg))
    for code, name in re.findall(
        r"self\._err\(\s*(\d{3}),\s*([A-Z][A-Z0-9_]*)\s*[,)]", m.group(0)
    ):
        assert name in EMU_CONSTS, f"{handler}: unknown constant {name}"
        out.add((int(code), EMU_CONSTS[name]))
    return out


def firmware_routes() -> list[tuple[str, str, str]]:
    return [
        (p, m, h)
        for p, m, h in re.findall(r'reg\("([^"]+)", HTTP_(\w+), (\w+)\);', SD_WEB)
    ]


FUNCS = c_functions(SD_WEB, SD_OTA, SD_SITE, SD_REMOTE, SD_UTIL)


def grab(pattern: str, text: str, group: int = 1) -> str:
    """One regex capture, or a loud failure naming what the parser expected."""
    m = re.search(pattern, text)
    assert m, f"firmware no longer matches /{pattern}/ — update the contract test"
    return m.group(group)


def stream_port() -> int:
    """The port the SECOND server listens on — sd_web_stream.h, every note
    of audio in the show. It is outside sd_web.h's reg() table, so the
    contract reads it here (grade report 2026-09-06 J3)."""
    return int(grab(r"cfg\.server_port = (\d+);", SD_STREAM))
