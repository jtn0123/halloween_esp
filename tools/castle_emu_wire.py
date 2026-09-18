"""What firmware/sd_web.h does with the bytes on the wire — ported verbatim.

The emulator (castle_emu.py) must never drift from the castle, so every rule
that decides a request's fate lives here, in one place, as a direct port of
the C it mirrors — the route table, esp_http_server's wildcard matcher and
its 404/405 verdicts, url_decode, safe_name, name_from_uri, query_param with
the httpd's buffer limits. tests/test_firmware_contract.py parses the C at
test time and holds this file to it.

Everything works on BYTES. The firmware sees raw octets: safe_name's
"size() < 100" counts UTF-8 bytes, not characters, and a bad %XX makes
url_decode fail empty rather than minting a NUL. A str port
would silently disagree with the board on exactly the inputs a fuzz throws.
"""

from __future__ import annotations

import json

#: esp_http_server's request-line ceiling (HTTPD_MAX_URI_LEN). Longer → 414.
MAX_URI = 512
#: query_param()'s stack buffers in sd_web.h: the whole query string, and
#: one value. A query at or over the buffer length is TRUNC → 414. The value
#: buffer was 120 until v5.60: safe_name admits a 99-character name, spaces
#: URL-encode to three bytes each, and the truncation came back as an empty
#: parameter — a 400 "need ?f=<file>" for a file /api/files had just listed
#: (A10). 3*99 + 2 now, past anything the query ceiling can deliver.
QUERY_BUF = 200
VALUE_BUF = 301
#: safe_name's / safe_subpath's length ceilings.
NAME_MAX = 100
SUBPATH_MAX = 140

#: The reg() table in castle_web::start(), in registration order. Handler
#: names are the firmware's, so a diff against sd_web.h reads one-to-one.
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("/api/status", "GET", "h_status"),
    ("/api/health", "GET", "h_health"),
    ("/api/events", "GET", "h_events"),
    ("/api/files", "GET", "h_list"),
    ("/api/files/*", "PUT", "h_put"),
    ("/api/site/*", "PUT", "h_put"),
    ("/api/scenes/*", "PUT", "h_put"),
    ("/api/files/*", "DELETE", "h_delete"),
    ("/api/site/*", "DELETE", "h_delete"),
    ("/api/scenes/*", "DELETE", "h_delete"),
    ("/api/play", "POST", "h_play"),
    ("/api/scene", "POST", "h_scene"),
    ("/api/stop", "POST", "h_stop"),
    ("/api/show/start", "POST", "h_show_start"),
    ("/api/show/stop", "POST", "h_show_stop"),
    ("/api/blackout", "POST", "h_blackout"),
    ("/api/blackout", "GET", "h_blackout"),
    ("/remote", "GET", "h_remote"),
    ("/api/volume", "POST", "h_volume"),
    ("/api/light", "POST", "h_light"),
    ("/api/pir", "POST", "h_pir"),
    ("/api/ota", "PUT", "h_ota"),
    ("/api/bootlog", "GET", "h_bootlog"),
    ("/sd/*", "GET", "h_sd_get"),
    ("/site/*", "GET", "h_site"),
    ("/", "GET", "h_root"),
)

#: esp_http_server's own error pages, for verdicts the firmware never sees:
#: an unparseable header, no route, wrong method, an oversized request line,
#: a body that stopped arriving. text/html there (HTTPD_TYPE_TEXT is
#: "text/html", not "text/plain" — the reply_err pages are the plain ones).
#:
#: Copied from httpd_resp_send_err's table in ESP-IDF 5.5.5
#: (components/esp_http_server/src/httpd_txrx.c), which is the framework
#: esphome pulls for this board. The wording here was IDF 4.x's until
#: tests/test_firmware_web_cxx.py ran the real headers beside this file and
#: found the two tables had drifted apart a major version ago.
IDF_ERRORS = {
    400: "Bad request syntax",
    404: "Nothing matches the given URI",
    405: "Specified method is invalid for this resource",
    408: "Server closed this connection",
    414: "URI is too long",
}


def wildcard_match(template: str, path: bytes) -> bool:
    """httpd_uri_match_wildcard, for the '*' form the firmware uses: an
    exact match, or a template ending in '*' whose stem prefixes the path.
    ("/api/files/*" does NOT match "/api/files" — the stem is 11 chars.)"""
    t = template.encode()
    if t.endswith(b"*"):
        return path.startswith(t[:-1])
    return path == t


def route(method: str, raw_target: bytes) -> tuple[str | None, int]:
    """(handler, 0) for a served (method, path); (None, 404|405) otherwise.

    The router matches the path BEFORE the first '?' of the undecoded
    request target (httpd_uri.c), so "%3F" in a filename is part of the
    path here and only becomes '?' once name_from_uri decodes it.
    """
    path = raw_target.split(b"?", 1)[0]
    verdict = 404
    for template, m, handler in ROUTES:
        if wildcard_match(template, path):
            if m == method:
                return handler, 0
            verdict = 405
    return None, verdict


def url_decode(raw: bytes) -> bytes:
    """sd_web.h url_decode: %XX and '+'. A '%' that is not followed by two
    hex bytes is a failure (empty result), not strtol's NUL — a TRAILING
    stub ("x%4", "x%") included, since the firmware stopped letting that one
    through as literal text (grade report 2026-09-17 J1). The NUL legs are
    the C reading a `const char *`: a name whose escape runs into one has
    ended there as far as the board is concerned."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == 0x25:  # '%'
            if i + 2 >= n or not raw[i + 1] or not raw[i + 2]:
                return b""
            if _hexval(raw[i + 1]) < 0 or _hexval(raw[i + 2]) < 0:
                return b""
            out.append((_hexval(raw[i + 1]) * 16 + _hexval(raw[i + 2])) & 0xFF)
            i += 3
        elif c == 0x2B:  # '+'
            out.append(0x20)
            i += 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


#: RFC 3986's unreserved set, plus the '/' url_encode leaves alone because
#: what it encodes is a card path rather than one name.
UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~/"
)


def url_encode(raw: bytes) -> bytes:
    """sd_web_util.h url_encode: the loopback URL the media player is handed
    for a card file. The emulator has no loopback hop of its own — no media
    player, no second server — so nothing here calls it; it is ported so the
    C has an oracle, because the bug it fixes was exactly a rule that lived
    in one language only (grade report 2026-09-17 J1)."""
    out = bytearray()
    for c in raw:
        if c in UNRESERVED:
            out.append(c)
        else:
            out += b"%%%02X" % c
    return bytes(out)


def _hexval(b: int) -> int:
    c = chr(b)
    return int(c, 16) if c in "0123456789abcdefABCDEF" else -1


def safe_name(n: bytes) -> bool:
    """One path component, nothing hidden, nothing that breaks the JSON it
    is later printed into — sd_web.h safe_name on the raw bytes. Control
    bytes (NUL included — the C length counts it), DEL, '"' and '\\' are
    refused because h_list/h_status snprintf names into JSON unescaped —
    and since v5.46 so is every byte >= 0x80, which json_escape passes
    through raw and which therefore made the body invalid UTF-8."""
    if not n or len(n) >= NAME_MAX or n[0:1] == b"." or b"/" in n:
        return False
    return not any(c < 0x20 or c >= 0x80 or c == 0x7F or c in (0x22, 0x5C) for c in n)


_ZONE_CHARS = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def light_spec_ok(c: bytes) -> bool:
    """sd_web_state.h light_spec_ok, byte for byte: "RRGGBB"|show|off with an
    optional "<zone>:" prefix that drives one strip (the desk's channel test).

    Here rather than beside the handler that calls it because it is a byte
    rule like the ones above it — a decision about a value, taken before any
    card or mailbox is touched — and this file is where the firmware's byte
    rules are ported."""
    zone, sep, spec = c.partition(b":")
    if not sep:
        zone, spec = b"", c
    elif not zone or len(zone) > 16 or any(b not in _ZONE_CHARS for b in zone):
        return False
    spec, at, pct = spec.partition(b"@")
    if at and (not pct.isdigit() or len(pct) > 3 or not 1 <= int(pct) <= 100):
        return False
    hex6 = len(spec) == 6 and all(chr(b) in "0123456789abcdefABCDEF" for b in spec)
    return hex6 or spec in (b"white", b"bars", b"chase", b"ends", b"show", b"off")


def safe_subpath(p: bytes) -> bool:
    """sd_web_util.h safe_subpath: subdirectories allowed; `.` / `..` /
    leading-dot names refused as whole path segments."""
    if not p or len(p) > SUBPATH_MAX or p[0:1] == b"/":
        return False
    return all(seg and not seg.startswith(b".") for seg in p.split(b"/"))


def route_dir(raw_target: bytes) -> tuple[str, bytes]:
    """sd_web.h route_dir: the card subdirectory a /api/files|site|scenes/*
    route addresses and the prefix to cut, shared by h_put and h_delete."""
    if raw_target.startswith(b"/api/site/"):
        return "site", b"/api/site/"
    if raw_target.startswith(b"/api/scenes/"):
        return "scenes", b"/api/scenes/"
    return "", b"/api/files/"


def name_from_uri(raw_target: bytes, prefix: bytes) -> bytes:
    """The filename after a fixed prefix: decode the WHOLE remaining target
    (query included), then cut at the first '?' of the decoded text."""
    n = url_decode(raw_target[len(prefix) :])
    q = n.find(b"?")
    return n[:q] if q >= 0 else n


def c_str(n: bytes) -> bytes:
    """What snprintf("%s", n.c_str()) keeps: everything before the first NUL."""
    z = n.find(b"\0")
    return n[:z] if z >= 0 else n


def fs_name(n: bytes) -> str:
    """The bytes a handler hands to the filesystem, as a Python path part."""
    return c_str(n).decode("utf-8", "surrogateescape")


def fat_path(n: bytes) -> str | None:
    """`n` as a card-relative path, or None when FatFs would not find it.

    ESP-IDF builds FatFs with FF_FS_RPATH = 0 (its ffconf.h), so "." is not
    a directory reference — it is looked up as an ordinary file name and
    never found — and a trailing separator demands that what precedes it be
    a directory before failing on the empty segment after it. Python's
    pathlib deletes both silently, so "GET /sd/a/" served the file `a` here
    and answered FR_NO_PATH on the board (found by the C harness's storm,
    tests/test_firmware_web_storm.py)."""
    name = fs_name(n)
    if not name or name.endswith("/"):
        return None
    if any(seg in (".", "..") for seg in name.split("/")):
        return None
    return name


def query_truncated(raw_target: bytes) -> bool:
    """httpd_req_get_url_query_str TRUNC when the query plus NUL exceeds 200."""
    if b"?" not in raw_target:
        return False
    qry = raw_target.split(b"?", 1)[1]
    return bool(qry) and len(qry) + 1 > QUERY_BUF


def pir_armed_ok(a: bytes) -> tuple[bool, bytes]:
    """sd_web_util.h pir_armed_ok: empty, or 1/true/on / 0/false/off → 1/0."""
    if not a:
        return True, a
    low = a.lower()
    if low in (b"1", b"true", b"on"):
        return True, b"1"
    if low in (b"0", b"false", b"off"):
        return True, b"0"
    return False, a


def pir_cooldown_ok(c: bytes) -> bool:
    return (not c) or c in (b"30", b"60", b"120")


def _param_value(qry: bytes, eq: int) -> bytes:
    """The value of the pair whose '=' sits at `eq`, through
    httpd_query_key_value's 301-byte buffer and then url_decode.

    That buffer's truncation leg is unreachable behind the 200-byte query
    ceiling and is kept only so the two buffers stay spelled the way the C
    spells them — the firmware answers its own 414 there, with the same
    message the query ceiling gives, so no input exists on which the two
    sides differ.
    """
    end = qry.find(b"&", eq + 1)
    val = qry[eq + 1 :] if end < 0 else qry[eq + 1 : end]
    if len(val) + 1 > VALUE_BUF:
        return b""
    return url_decode(val)


def query_param(raw_target: bytes, key: str) -> bytes:
    """sd_web_util.h query_param: httpd_req_get_url_query_str into a 200-byte
    buffer, httpd_query_key_value into a 301-byte one (either truncation
    → ""), then url_decode. Keys compare case-insensitively; a pair without
    '=' derails the scan (the '=' found belongs to the NEXT pair).
    """
    if b"?" not in raw_target:
        return b""
    qry = raw_target.split(b"?", 1)[1]
    if not qry or len(qry) + 1 > QUERY_BUF:
        return b""
    k = key.encode().lower()
    pos = 0
    while pos < len(qry):
        eq = qry.find(b"=", pos)
        if eq < 0:
            break
        if eq - pos == len(k) and qry[pos:eq].lower() == k:
            return _param_value(qry, eq)
        amp = qry.find(b"&", eq + 1)
        if amp < 0:
            break
        pos = amp + 1
    return b""


def json_escape(s: str) -> str:
    """sd_web.h json_escape: the body of a JSON string literal. The C
    escapes '"', '\\', \\b \\f \\n \\r \\t and \\u00XX for the other
    control bytes, and passes everything else (UTF-8, DEL) through raw —
    exactly json.dumps's non-ASCII-preserving table, so this IS json.dumps."""
    return json.dumps(s, ensure_ascii=False)[1:-1]
