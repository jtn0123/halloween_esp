"""What firmware/sd_web.h does with the bytes on the wire — ported verbatim.

The emulator (castle_emu.py) must never drift from the castle, so every rule
that decides a request's fate lives here, in one place, as a direct port of
the C it mirrors — the route table, esp_http_server's wildcard matcher and
its 404/405 verdicts, url_decode, safe_name, name_from_uri, query_param with
the httpd's buffer limits. tests/test_firmware_contract.py parses the C at
test time and holds this file to it.

Everything works on BYTES. The firmware sees raw octets: safe_name's
"size() < 100" counts UTF-8 bytes, not characters, and url_decode can mint
a NUL ("%zz" → strtol → 0) that later truncates the C string. A str port
would silently disagree with the board on exactly the inputs a fuzz throws.
"""

from __future__ import annotations

import json

#: esp_http_server's request-line ceiling (HTTPD_MAX_URI_LEN). Longer → 414.
MAX_URI = 512
#: query_param()'s stack buffers in sd_web.h: the whole query string, and
#: one value. A query at or over the buffer length is TRUNC → "".
QUERY_BUF = 200
VALUE_BUF = 120
#: safe_name's / safe_subpath's length ceilings.
NAME_MAX = 100
SUBPATH_MAX = 140

#: The reg() table in castle_web::start(), in registration order. Handler
#: names are the firmware's, so a diff against sd_web.h reads one-to-one.
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("/api/status", "GET", "h_status"),
    ("/api/health", "GET", "h_health"),
    ("/api/files", "GET", "h_list"),
    ("/api/files/*", "PUT", "h_put"),
    ("/api/site/*", "PUT", "h_put"),
    ("/api/scenes/*", "PUT", "h_put"),
    ("/api/files/*", "DELETE", "h_delete"),
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

#: esp_http_server's own error pages (httpd_txrx.c), for verdicts the
#: firmware never sees: an unparseable header, no route, wrong method, an
#: oversized request line, a body that stopped arriving. text/html there.
IDF_ERRORS = {
    400: "Server unable to understand request due to invalid syntax",
    404: "This URI does not exist",
    405: "Request method for this URI is not handled by server",
    408: "Server closed this connection due to timeout",
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
    """sd_web.h url_decode: %XX and '+'. A '%' followed by two non-hex
    bytes is strtol → 0 — a NUL lands in the name, exactly as on the
    board (where the C string then ends there)."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == 0x25 and i + 2 < n and raw[i + 1] and raw[i + 2]:      # '%'
            out.append(_strtol16(raw[i + 1:i + 3]) & 0xFF)
            i += 3
        elif c == 0x2B:                                                # '+'
            out.append(0x20)
            i += 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _strtol16(two: bytes) -> int:
    """strtol(hex, nullptr, 16) on a 2-byte buffer: leading hex digits
    only, 0 when there are none."""
    val = 0
    for b in two:
        d = _hexval(b)
        if d < 0:
            break
        val = val * 16 + d
    return val


def _hexval(b: int) -> int:
    c = chr(b)
    return int(c, 16) if c in "0123456789abcdefABCDEF" else -1


def safe_name(n: bytes) -> bool:
    """One path component, nothing hidden, nothing that breaks the JSON it
    is later printed into — sd_web.h safe_name on the raw bytes. Control
    bytes (NUL included — the C length counts it), DEL, '"' and '\\' are
    refused because h_list/h_status snprintf names into JSON unescaped."""
    if not n or len(n) >= NAME_MAX or n[0:1] == b"." or b"/" in n or b".." in n:
        return False
    return not any(c < 0x20 or c == 0x7F or c in (0x22, 0x5C) for c in n)


def safe_subpath(p: bytes) -> bool:
    """sd_web_site.h safe_subpath: subdirectories allowed, no escapes."""
    if not p or len(p) > SUBPATH_MAX or p[0:1] in (b"/", b"."):
        return False
    return b".." not in p


def name_from_uri(raw_target: bytes, prefix: bytes) -> bytes:
    """The filename after a fixed prefix: decode the WHOLE remaining target
    (query included), then cut at the first '?' of the decoded text."""
    n = url_decode(raw_target[len(prefix):])
    q = n.find(b"?")
    return n[:q] if q >= 0 else n


def c_str(n: bytes) -> bytes:
    """What snprintf("%s", n.c_str()) keeps: everything before the first NUL."""
    z = n.find(b"\0")
    return n[:z] if z >= 0 else n


def fs_name(n: bytes) -> str:
    """The bytes a handler hands to the filesystem, as a Python path part."""
    return c_str(n).decode("utf-8", "surrogateescape")


def query_param(raw_target: bytes, key: str) -> bytes:
    """sd_web.h query_param: httpd_req_get_url_query_str into a 200-byte
    buffer, httpd_query_key_value into a 120-byte one (either truncation
    → ""), then url_decode. Keys compare case-insensitively; a pair without
    '=' derails the scan (the '=' found belongs to the NEXT pair)."""
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
            end = qry.find(b"&", eq + 1)
            val = qry[eq + 1:] if end < 0 else qry[eq + 1:end]
            if len(val) + 1 > VALUE_BUF:
                return b""
            return url_decode(val)
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
