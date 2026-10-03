"""What an import failure SAYS to the person who owns the castle.

A tool's whole output in, one plain sentence out: what happened, then what
to do next. No stack traces, no exit codes, no exception names — the raw
output stays in the log and behind the pages' "Details" disclosure, where
whoever helps the owner can read it.

This is the Python copy of `core/src/studio_reason.rs` (the scanner) and
`core/src/studio_reason_words.rs` (the words). The Rust studio explains the
desk's jobs; this copy explains Castle Radio's and the importer's own
failures. They are held together by `tests/test_import_reason.py`: the
same corpus (`tests/import_reasons.json`) through both, and the table below
compared with the Rust one line for line (docs/PARITY.md).

The scan never reads a line that starts with whitespace: that is a quote
of a tool (the importer's detail(), a yt-dlp line relayed while it ran, a
traceback's frames), kept for Details. Of the rest, in order: a last line
already in the owner's form — "what happened — what to do", the importer's
own verdict — as it stands; else a phrase from KNOWN anywhere outside the `[…]`
progress chatter (case ignored — yt-dlp's capitalisation drifts between
releases); else the last `ERROR:` line, which is yt-dlp's (a `[site]`-tagged
one is an extractor the site has outgrown, any other a download that did not
finish); else a Python traceback's last line (a program that failed, or a
crash); else the last meaningful line, which is where the importer's own
sentences land. Paths in a passed-through line come back as basenames.
"""

from __future__ import annotations

import errno

# ---- the sentences (studio_reason_words.rs spells each one identically) ----
DOWNLOADER_OLD = (
    "The downloader may be out of date — Update the downloader, then try the "
    "link again."
)
DOWNLOADER_MISSING = (
    "The song downloader is not installed yet — Update the downloader to "
    "install it, then try the link again."
)
DOWNLOAD_FAILED = (
    "The download did not finish — try the link again, and if it keeps failing, "
    "Update the downloader."
)
DISK_FULL = "The disk is full — free up some space on this computer, then try again."
NO_ACCESS = (
    "Castle Tools was not allowed to read or save a file it needed — copy the "
    "song to a folder on this computer and choose it again."
)
OUT_OF_MEMORY = (
    "The computer ran out of memory — close other apps, or try a shorter song."
)
FFMPEG_MISSING = (
    "The audio converter (ffmpeg) is missing — reinstall Castle Tools to put it back."
)
DEMUCS_MISSING = (
    "Voice separation is not installed — reinstall Castle Tools, or import with "
    "voice separation turned off."
)
START_FAILED = (
    "Castle Tools could not start its audio tools — reinstall Castle Tools to "
    "repair them."
)
PRIVATE = "That video is private — try a different link."
MEMBERS = "That video is for channel members only — try a different link."
BOT_CHECK = (
    "The website asked to check that you are not a robot — wait an hour and try "
    "again, or try a different link."
)
SIGN_IN = (
    "That website wants a signed-in account before it will share this video — "
    "try a different link."
)
GEO = "That video is blocked in your country — try a different link."
NOT_YET = (
    "That video has not started yet — try again once the premiere or live "
    "stream is over."
)
DRM = "That video is copy-protected and cannot be downloaded — try a different link."
UNAVAILABLE = (
    "That video is unavailable and may have been removed — try a different link."
)
NOT_A_LINK = (
    "That does not look like a complete link — copy the whole address from your "
    "browser and paste it again."
)
UNSUPPORTED_SITE = (
    "The downloader does not know that website — try a link from a different site."
)
NOT_FOUND = (
    "That link leads to a page that does not exist — check the link and try again."
)
TOO_MANY = "The website is refusing downloads for now — wait an hour, then try again."
SITE_DOWN = "The website is having trouble right now — try again later."
NETWORK = (
    "Could not reach that website — check that this computer is online, then try again."
)
NO_AUDIO = "The download finished without any audio — try a different link."
CONVERT_FAILED = (
    "The downloaded audio could not be converted — try the link again, or a "
    "different link."
)
STALLED = "The import took too long and was stopped — try again, or try a shorter song."
GENERIC = (
    "The import failed for a reason Castle Tools does not recognise — try "
    "again, and open Details if it keeps failing."
)
#: `{prog}` is the program that failed (ffmpeg, yt-dlp), never its path.
TOOL_FAILED = (
    "{prog} stopped with an error — try again, and if it keeps failing, try a "
    "different copy of the song."
)

#: Phrases worth a sentence, first match wins. The owner's own machine
#: first (a full disk breaks every step, and every tool says so in its own
#: words), then what the website said about the video, then the link, then
#: the signs of a downloader the site has outgrown, then the network, then
#: the audio itself.
KNOWN: tuple[tuple[str, str], ...] = (
    ("No space left on device", DISK_FULL),
    ("not enough space on the disk", DISK_FULL),
    ("Disk quota exceeded", DISK_FULL),
    ("Read-only file system", NO_ACCESS),
    ("Permission denied", NO_ACCESS),
    ("Access is denied", NO_ACCESS),
    ("Operation not permitted", NO_ACCESS),
    ("out of memory", OUT_OF_MEMORY),
    ("MemoryError", OUT_OF_MEMORY),
    ("Cannot allocate memory", OUT_OF_MEMORY),
    ("memory allocation of", OUT_OF_MEMORY),
    ("No such file or directory: 'ffmpeg'", FFMPEG_MISSING),
    ("No such file or directory: 'ffprobe'", FFMPEG_MISSING),
    ("ffmpeg: command not found", FFMPEG_MISSING),
    ("ffmpeg not found", FFMPEG_MISSING),
    ("No module named demucs", DEMUCS_MISSING),
    ("No module named 'demucs'", DEMUCS_MISSING),
    ("No such file or directory: 'yt-dlp'", DOWNLOADER_MISSING),
    ("yt-dlp: command not found", DOWNLOADER_MISSING),
    ("Private video", PRIVATE),
    ("members-only", MEMBERS),
    ("not a bot", BOT_CHECK),
    ("Sign in to confirm", SIGN_IN),
    ("in your country", GEO),
    ("from your location", GEO),
    ("geo restrict", GEO),
    ("live event will begin", NOT_YET),
    ("Premieres in", NOT_YET),
    ("Premiere will begin", NOT_YET),
    ("DRM protected", DRM),
    ("Video unavailable", UNAVAILABLE),
    ("video has been removed", UNAVAILABLE),
    ("is not a valid URL", NOT_A_LINK),
    ("Unsupported URL", UNSUPPORTED_SITE),
    ("HTTP Error 404", NOT_FOUND),
    ("HTTP Error 410", NOT_FOUND),
    ("HTTP Error 429", TOO_MANY),
    ("HTTP Error 500", SITE_DOWN),
    ("HTTP Error 502", SITE_DOWN),
    ("HTTP Error 503", SITE_DOWN),
    ("HTTP Error 504", SITE_DOWN),
    ("HTTP Error 403", DOWNLOADER_OLD),
    ("Requested format", DOWNLOADER_OLD),
    ("No video formats found", DOWNLOADER_OLD),
    ("Only images are available", DOWNLOADER_OLD),
    ("Unable to extract", DOWNLOADER_OLD),
    ("Failed to extract", DOWNLOADER_OLD),
    ("nsig extraction failed", DOWNLOADER_OLD),
    ("Signature extraction failed", DOWNLOADER_OLD),
    ("Precondition check failed", DOWNLOADER_OLD),
    ("Confirm you are on the latest version", DOWNLOADER_OLD),
    ("please report this issue", DOWNLOADER_OLD),
    ("Temporary failure in name resolution", NETWORK),
    ("nodename nor servname", NETWORK),
    ("Name or service not known", NETWORK),
    ("getaddrinfo failed", NETWORK),
    ("Failed to resolve", NETWORK),
    ("Network is unreachable", NETWORK),
    ("No route to host", NETWORK),
    ("Connection refused", NETWORK),
    ("Connection reset", NETWORK),
    ("Remote end closed connection", NETWORK),
    ("CERTIFICATE_VERIFY_FAILED", NETWORK),
    ("timed out", NETWORK),
    ("Did not get any data blocks", NETWORK),
    ("Unable to download webpage", NETWORK),
    ("no audio file", NO_AUDIO),
    ("audio conversion failed", CONVERT_FAILED),
)

#: The failures one button fixes: the page offers "Update the downloader".
UPDATE_DOWNLOADER = "update-downloader"

_EXC_TAIL = ("Error", "Exception", "Exit", "Interrupt")
#: Rust's u8::is_ascii_whitespace — note: no vertical tab.
_SPACE = " \t\n\x0c\r"


def _word(c: str) -> bool:
    """`is_ascii_alphanumeric() || c == '_'` — ASCII only, like the Rust."""
    return c == "_" or (c.isascii() and c.isalnum())


def _chatter(line: str) -> bool:
    """A progress or info line (`[download] Destination: …`): it names the
    song, never the failure, so a title that says "Private Video" or "Timed
    Out" cannot pass for one."""
    return line.startswith("[")


def _sep(c: str) -> bool:
    return c in "/\\"


def _starts_a_path(s: str, i: int) -> bool:
    """A '/' (or '\\') opens a path unless the character before it is one the
    old regex's lookbehind excluded — which is what keeps URLs whole."""
    if i == 0:
        return True
    p = s[i - 1]
    return not (p == ":" or _sep(p) or _word(p))


def _path_prefix_end(s: str, i: int) -> int | None:
    """The last separator of the `[^/\\s'"]+/` run starting at `i`, taken
    possessively; None when there is no run. A doubled backslash (a Python
    repr of a Windows path) counts as one separator."""
    j, last, seg = i + 1, None, 0
    while j < len(s):
        c = s[j]
        if _sep(c):
            if seg == 0:
                if c == "\\" and s[j - 1] == "\\":
                    last = j
                    j += 1
                    continue
                break
            last, seg = j, 0
            j += 1
        elif c in _SPACE or c in "'\"":
            break
        else:
            seg += 1
            j += 1
    return last


def _drive_prefix_end(s: str, i: int) -> int | None:
    """A drive path (`C:\\…`, `C:/…`) starting at `i` and where its prefix
    ends; never a URL scheme, never a letter inside a word."""
    letter = (
        s[i].isascii()
        and s[i].isalpha()
        and s[i + 1 : i + 2] == ":"
        and _sep(s[i + 2 : i + 3] or "x")
    )
    if not letter or (i > 0 and _word(s[i - 1])):
        return None
    end = _path_prefix_end(s, i + 2)
    return i + 2 if end is None else end


def basenames(s: str) -> str:
    """'/a/b/x.wav' → 'x.wav', 'C:\\Users\\me\\x.wav' → 'x.wav'; URLs whole."""
    out, i = [], 0
    while i < len(s):
        if _sep(s[i]) and _starts_a_path(s, i):
            end = _path_prefix_end(s, i)
            if end is not None:
                i = end + 1
                continue
        end = _drive_prefix_end(s, i)
        if end is not None:
            i = end + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _exc_match(line: str) -> tuple[str, str | None] | None:
    """`^([A-Za-z_][\\w.]*)(?::\\s*(.*))?$`, ASCII word characters."""
    if not line or not (line[0] == "_" or (line[0].isascii() and line[0].isalpha())):
        return None
    i = 1
    while i < len(line) and (_word(line[i]) or line[i] == "."):
        i += 1
    if i == len(line):
        return line, None
    if line[i] != ":":
        return None
    return line[:i], line[i + 1 :].lstrip(_SPACE)


def _strip_exe(prog: str) -> str:
    return prog[:-4] if len(prog) > 4 and prog[-4:].lower() == ".exe" else prog


def _exception_line(rest: str | None) -> str:
    """The program a CalledProcessError names, as a sentence; any other
    exception is a crash, whose message is for the log, not the owner."""
    rest = (rest or "").strip(_SPACE)
    at = rest.find("Command '['")
    if at >= 0:
        after = rest[at + 11 :]
        end = after.find("'")
        if end >= 0:
            prog = _strip_exe(after[:end].replace("\\", "/").rsplit("/", 1)[-1])
            if prog:
                return TOOL_FAILED.format(prog=prog)
    return GENERIC


def _quoted(line: str) -> bool:
    """A line that starts with whitespace quotes a tool — detail(), a line
    relayed from yt-dlp, a traceback's frames — for whoever helps; the
    verdict is never read from one."""
    return not line or line[0] in _SPACE


def _verdict(line: str) -> bool:
    """Already the owner's sentence — what happened — what to do — rather
    than an exception's message that happens to hold a dash."""
    m = _exc_match(line)
    return " — " in line and not (m and m[0].endswith(_EXC_TAIL))


def _known(heard: list[str]) -> str:
    """The sentence for a KNOWN phrase anywhere in what was said."""
    text = "\n".join(heard).lower()
    return next((friendly for needle, friendly in KNOWN if needle.lower() in text), "")


def _download_error(heard: list[str]) -> str:
    """yt-dlp's last `ERROR:` line: a `[site]`-tagged one is an extractor
    the site has outgrown, any other a download that did not finish."""
    for line in reversed(heard):
        if "ERROR:" in line:
            tail = line.split("ERROR:", 1)[1].strip(_SPACE)
            return DOWNLOADER_OLD if tail.startswith("[") else DOWNLOAD_FAILED
    return ""


def _crash(said: list[str]) -> str:
    """A Python traceback's last line: a program that failed, or a crash."""
    for line in reversed(said):
        m = _exc_match(line)
        if m and m[0].endswith(_EXC_TAIL):
            return _exception_line(m[1])
    return ""


def _scan(log: list[str], passthrough: bool) -> str:
    said = [ln for ln in log if not _quoted(ln)]
    heard = [ln for ln in said if not _chatter(ln)]
    meant = [ln for ln in heard if not ln.startswith("Traceback")]
    last = meant[-1].strip(_SPACE) if meant else ""
    if passthrough and _verdict(last):
        return basenames(last)
    found = _known(heard) or _download_error(heard) or _crash(said)
    if found or not passthrough:
        return found
    return basenames(last)


def explain(log: list[str]) -> str:
    """One sentence worth showing a person, or "" when the log holds none."""
    return _scan(log, passthrough=True)


def _lines(text: str) -> list[str]:
    """Rust's str::lines, blank ones dropped: split on \\n only (a lone \\r
    or a form feed is not a line break there), trailing space trimmed."""
    lines = [ln.rstrip(_SPACE) for ln in text.split("\n")]
    return [ln for ln in lines if ln.strip(_SPACE)]


def reason(text: str) -> str:
    """The verdict for a tool's whole output, blank lines and all."""
    return explain(_lines(text))


def recognised(text: str) -> str:
    """Like reason(), but only a sentence from this module's own vocabulary
    — never a passed-through line. For a caller that has a better sentence
    of its own when the tool's output says nothing recognisable."""
    return _scan(_lines(text), passthrough=False)


def action(sentence: str) -> str | None:
    """The one-button fix a sentence offers, if any."""
    if sentence in (DOWNLOADER_OLD, DOWNLOADER_MISSING, DOWNLOAD_FAILED):
        return UPDATE_DOWNLOADER
    return None


#: errno values that mean the same as a KNOWN phrase, for an OSError caught
#: in-process (a write that failed, not a tool that said so). Windows'
#: ERROR_DISK_FULL (112) and ERROR_HANDLE_DISK_FULL (39) arrive as winerror.
_ERRNO = {
    errno.ENOSPC: DISK_FULL,
    getattr(errno, "EDQUOT", errno.ENOSPC): DISK_FULL,
    errno.EACCES: NO_ACCESS,
    errno.EPERM: NO_ACCESS,
    getattr(errno, "EROFS", errno.EACCES): NO_ACCESS,
    errno.ENOMEM: OUT_OF_MEMORY,
}
_WINERROR = {112: DISK_FULL, 39: DISK_FULL, 5: NO_ACCESS}


def for_os_error(exc: OSError) -> str | None:
    """The sentence an in-process OSError deserves, or None when it is not
    one the owner can act on in its own right (a missing file, say)."""
    win = getattr(exc, "winerror", None)
    if win in _WINERROR:
        return _WINERROR[win]
    return _ERRNO.get(exc.errno or 0)


def owner_failure(output: str) -> tuple[str, str | None]:
    """(sentence, action) for a failed tool's output: never empty."""
    said = reason(output) or GENERIC
    return said, action(said)
