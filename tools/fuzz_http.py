"""One HTTP request on a bare socket — the layer under the protocol fuzz.

Split from castle_fuzz.py at the 500-line cap along the seam that was
already there: this is the wire (a request whose line and headers can lie,
and the retry/timeout judgement calls around reading the answer); what to
send and which invariants to hold stays next door in castle_fuzz.py.
"""

from __future__ import annotations

import socket
import time


class Violation(AssertionError):
    pass


class SlowRead(Exception):
    """The response was still arriving when our socket timer fired.

    Load, not a verdict — the caller retries. A server that is genuinely stuck
    keeps doing it, and the retries run out.
    """


def raw_request(
    host: str,
    port: int,
    method: str,
    target: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    declared: int | None = None,
    send_fraction: float = 1.0,
    hang: bool = False,
    timeout: float = 8.0,
) -> tuple[int, bytes, dict[str, str]]:
    """One request on a bare socket, so the line and headers can lie.

    `send_fraction` < 1 sends only the head of the body; with `hang` the
    socket then stays open and silent (slow-loris — the server's recv
    timer must fire), otherwise the client half-closes and the server
    sees EOF at once."""
    hdrs = {"Host": "castle", "Connection": "close"}
    n = len(body) if declared is None else declared
    if method in ("PUT", "POST") or body:
        hdrs["Content-Length"] = str(n)
    hdrs.update(headers or {})  # the caller's lies win
    head = f"{method} {target} HTTP/1.1\r\n".encode("latin-1")
    head += "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()).encode() + b"\r\n"
    s = _connect(host, port, timeout)
    data = b""
    try:
        try:
            s.sendall(head + body[: int(len(body) * send_fraction)])
            if not hang:
                s.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # the server may reply-and-close before the body is in
        timed_out = False
        while True:
            try:
                chunk = s.recv(65536)
            except TimeoutError:
                timed_out = True
                break
            except (ConnectionResetError, BrokenPipeError):
                break
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    if not data:
        # RST: the server closed with unread request bytes in its buffer
        # (a reply sent before the body was consumed). Code 0 = "reset";
        # the caller decides whether that was a legitimate moment.
        return 0, b"", {}
    if not data.startswith(b"HTTP/"):
        raise Violation(f"garbage reply to {method} {target!r}: {data[:80]!r}")
    head_b, sep, payload = data.partition(b"\r\n\r\n")
    lines = head_b.split(b"\r\n")
    code = int(lines[0].split()[1])
    hdr = {
        k.strip().lower(): v.strip()
        for k, v in (ln.decode("latin-1").partition(":")[::2] for ln in lines[1:])
    }
    # Our own clock running out mid-answer is not the server's verdict. Half a
    # JSON body reads exactly like a malformed one, and under CI load that is
    # what it was reported as (2026-08-22): "unparseable JSON" for a body the
    # emulator had every intention of finishing. Say which it was.
    declared_len = hdr.get("content-length")
    short = declared_len is not None and len(payload) < int(declared_len)
    if timed_out and (not sep or short):
        raise SlowRead(f"{method} {target!r}: read timed out after {len(data)} bytes")
    return code, payload, hdr


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    """A listen backlog overflows under a storm (macOS answers with RST or
    refusal); that is load, not a verdict — retry briefly."""
    for attempt in range(20):
        try:
            return socket.create_connection((host, port), timeout=timeout)
        except OSError:  # both refusals and resets are OSErrors
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")
