"""What a radio request must pass before a route runs.

The server binds loopback, but loopback is not a boundary a browser respects:
any page the operator has open can post to 127.0.0.1:8871, and a POST the
browser considers "simple" — a form-shaped content type, no custom headers —
is sent without asking this server first. Since there is no `do_OPTIONS` here,
the cheapest boundary is to make every state-changing route un-simple, so the
browser must preflight it and is refused by the silence (grade report
2026-09-17 E2). That is the whole of `json_type` and `marker`.

`queue_room` is the other half of the same item: one worker, an unbounded
submit queue, and 100 MB an upload.
"""

CONTENT_TYPE_JSON = "application/json"
MARKER_HEADER = "X-Castle"
MARKER_VALUE = "1"
#: Unfinished jobs the single worker will hold before the next one is refused.
QUEUE_LIMIT = 8
UPLOAD_SUFFIXES = (".mp3", ".wav", ".flac", ".opus", ".m4a", ".ogg", ".aac")


class Refused(Exception):
    """A request this server will not run, carrying the status it answers
    with. The routes' own failures stay plain ValueError/OSError."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def json_type(value: str | None) -> None:
    """Refuse a JSON route that was not asked as JSON. `application/json` is
    not one of the three types a cross-origin form can post without a
    preflight, so requiring it is what makes these routes un-simple; the
    `; charset=…` a library may append is still the same type."""
    if (value or "").partition(";")[0].strip().lower() != CONTENT_TYPE_JSON:
        raise Refused("Send this request as application/json.", 415)


def marker(value: str | None) -> None:
    """Refuse a route that carries no body worth typing (the raw upload, a
    restore) unless it names itself with a header no form can set. X-Filename
    was nearly this already, but the upload route falls back to a default
    name, so it was never required — this one is."""
    if value != MARKER_VALUE:
        raise Refused(
            f"Import from the Castle Radio page: {MARKER_HEADER} is required.", 403
        )


def queue_room(pending: int) -> None:
    """Refuse a job that would join a full queue. Jobs run one at a time and
    an upload is up to 100 MB, so an unbounded queue is unbounded disk."""
    if pending >= QUEUE_LIMIT:
        raise Refused(
            f"{QUEUE_LIMIT} imports are already waiting — let them finish first.",
            429,
        )


def upload_suffix(head: bytes, declared: str) -> str:
    """The suffix an upload is stored under, read from its first bytes. The
    browser's filename only says which of the known suffixes to assume when
    the bytes are not recognisable; the header's text never names a file."""
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"OggS":
        return ".opus" if b"OpusHead" in head[:128] else ".ogg"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return ".aac"
    if declared not in UPLOAD_SUFFIXES:
        raise ValueError("Choose an MP3, WAV, FLAC, Opus, M4A, OGG, or AAC file.")
    return UPLOAD_SUFFIXES[UPLOAD_SUFFIXES.index(declared)]
