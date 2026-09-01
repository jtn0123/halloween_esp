"""What the protocol fuzz SENDS: the alphabet, the shapes, and the one
predicate about a name that is a property of the name rather than of any
answer.

Second split out of castle_fuzz.py, on the generator/oracle seam (grade
report 2026-09-01 I1) — the first was tools/fuzz_http.py, which took the
transport. What is left next door is the oracle: which verdict each
request must draw, and the invariants the storm holds. Nothing here
knows there is a server.

Deliberately stateless and rng-in, string-out: every generator takes the
`random.Random` it should draw from, so a thread's stream stays its own
and a seeded run replays exactly. That is the whole reason these were
worth moving rather than merely shortening.
"""

from __future__ import annotations

import random

#: What a fuzz name is built from: ASCII, unicode (2- and 3-byte UTF-8),
#: the percent sign with valid and invalid hex, '+', dots, slashes.
ATOMS = [
    "a",
    "b",
    "Z",
    "0",
    "_",
    "-",
    "%20",
    ".",
    "..",
    "/",
    "%2F",
    "%2e%2e",
    "%00",
    "%zz",
    "%4",
    "+",
    "?",
    "%3F",
    "é",
    "ü",
    "名",
    "%C3%A9",
    "%E5%90%8D",
    "'",
    "(",
    ")",
    "[",
    "]",
    "~",
    "!",
    "$",
    "&",
    "=",
    ";",
    ".mp3",
    "%ff",
    "%80",
    "%0a",
    "%0d",
]

#: Decoded bytes the fuzz keeps OFF the card: the firmware's unescaped JSON
#: listing breaks on them (a reported firmware bug; see
#: tests/test_firmware_contract.py). Names holding them are only DELETEd.
POISON = b'"\\' + bytes(range(0x20))

#: The only 5xx bodies the firmware is allowed to produce; anything else
#: with a 5xx code is a finding.
DOCUMENTED_5XX = {
    b"short write",
    b"cannot create file",
    b"ota write failed",
    b"no SD card",
}

VERBS = ["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]

#: Paths that are near-misses for a real route — the routing table's edges,
#: where a trailing slash, a case change or one extra letter must still
#: land on the documented 404/405 rather than on a handler.
NEAR_MISSES = [
    "/api",
    "/api/",
    "/api/filesx",
    "/nope",
    "/sd",
    "/site",
    "/api/status/",
    "/API/status",
    "/remote/",
]

#: (route, query key, values that are interesting for it) — the good, the
#: nearly-good and the outright wrong, so the oracle next door has
#: something to disagree with.
QUERY_ROUTES = [
    ("/api/volume", "v", ["0", "70", "100", "007", "101", "-1", "1e2"]),
    ("/api/scene", "s", ["vigil", "storm", "nope", "stop"]),
    ("/api/light", "c", ["show", "off", "ff00aa", "FF00AA", "ff00a", "gggggg"]),
    ("/api/pir", "armed", ["0", "1", "x"]),
    ("/api/play", "f", ["wicked_winds.mp3", "..", ".x"]),
]

#: PUT body sizes, straddling the firmware's 8 KB read chunk on both sides.
BODY_SIZES = [0, 1, 8191, 8192, 8193, 65536]


def poisoned_text(n: bytes) -> bool:
    """Would this name, snprintf'd raw into the firmware's JSON, break it?
    Quotes, backslashes, control bytes — and bytes that are not UTF-8,
    which no JSON parser will take either."""
    if any(b in POISON for b in n):
        return True
    try:
        n.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def name(rng: random.Random) -> str:
    """A filename made of 1 to 40 atoms — the fuzz's whole vocabulary of
    ways a name can be hostile, in every order."""
    k = rng.choice([1, 2, 3, 5, 8, 20, 40])
    return "".join(rng.choice(ATOMS) for _ in range(k))


def query(rng: random.Random, key: str, good: list[str]) -> str:
    """A query string for `key`: usually well-formed, sometimes missing,
    duplicated, empty, overlong, wrong-cased, prefixed with a junk pair,
    or a fuzz name where a value belongs."""
    mode = rng.random()
    if mode < 0.25:
        return f"?{key}={rng.choice(good)}"
    if mode < 0.35:
        return ""  # missing
    if mode < 0.45:
        return f"?{key}={rng.choice(good)}&{key}={name(rng)}"  # dup
    if mode < 0.55:
        return f"?{key}="  # empty
    if mode < 0.62:
        return "?" + key + "=" + "x" * rng.choice([118, 119, 120, 198, 199, 300, 600])
    if mode < 0.70:
        return f"?{key.upper()}={rng.choice(good)}"  # case
    if mode < 0.78:
        return f"?junk&{key}={rng.choice(good)}"  # derailer
    return f"?{key}={name(rng)}"
