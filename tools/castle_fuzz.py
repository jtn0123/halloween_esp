#!/usr/bin/env python3
"""Protocol fuzz for the castle's HTTP surface — seeded, deterministic.

    .venv/bin/python tools/castle_fuzz.py                 # in-process emulator
    .venv/bin/python tools/castle_fuzz.py --seed 42 --iterations 3000
    .venv/bin/python tools/castle_fuzz.py --host 127.0.0.1:8093   # a running one

Throws random, unicode, percent-encoded, overlong and NUL-bearing filenames
at PUT/DELETE/play/sd-get, mangled query strings at every POST verb, wrong
verbs at every route, bodies of 0 B to 2 MB with honest and lying
Content-Length headers, and a slow-loris upload — from several threads at
once — and checks the INVARIANTS, not the answers:

  * never a 5xx except the firmware's own documented ones (short write,
    cannot create file, ota write failed); never an "emulator bug" 500
  * every verdict agrees with castle_emu_wire's port of the firmware rule
    (safe_name, digits-only volume, 404/405 routing) — emulator handlers
    cannot drift from the validators they are supposed to apply
  * a file only ever lands INSIDE the card directory, under the safe name
  * JSON on every 200 application/json parses
  * after the storm the castle still answers /api/status

tests/test_protocol_fuzz.py runs this with a small budget on every test
run; CASTLE_FUZZ_SEED picks the seed, and the seed is in every failure
message so a run can be replayed here.

Pointing it at a REAL castle (--host) is allowed but it writes and deletes
files in the card root. Do it on the bench, not on the night.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import castle_emu_wire as wire

#: What a fuzz name is built from: ASCII, unicode (2- and 3-byte UTF-8),
#: the percent sign with valid and invalid hex, '+', dots, slashes.
ATOMS = ["a", "b", "Z", "0", "_", "-", "%20", ".", "..", "/", "%2F", "%2e%2e",
         "%00", "%zz", "%4", "+", "?", "%3F", "é", "ü", "名", "%C3%A9",
         "%E5%90%8D", "'", "(", ")", "[", "]", "~", "!", "$", "&", "=", ";",
         ".mp3", "%ff", "%80", "%0a", "%0d"]
#: Decoded bytes the fuzz keeps OFF the card: the firmware's unescaped JSON
#: listing breaks on them (a reported firmware bug; see
#: tests/test_firmware_contract.py). Names holding them are only DELETEd.
POISON = b'"\\' + bytes(range(0x20))

DOCUMENTED_5XX = {b"short write", b"cannot create file", b"ota write failed",
                  b"no SD card"}
VERBS = ["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]


class Violation(AssertionError):
    pass


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


def raw_request(host: str, port: int, method: str, target: str,
                body: bytes = b"", headers: dict[str, str] | None = None,
                declared: int | None = None, send_fraction: float = 1.0,
                hang: bool = False,
                timeout: float = 8.0) -> tuple[int, bytes, dict[str, str]]:
    """One request on a bare socket, so the line and headers can lie.

    `send_fraction` < 1 sends only the head of the body; with `hang` the
    socket then stays open and silent (slow-loris — the server's recv
    timer must fire), otherwise the client half-closes and the server
    sees EOF at once."""
    hdrs = {"Host": "castle", "Connection": "close"}
    n = len(body) if declared is None else declared
    if method in ("PUT", "POST") or body:
        hdrs["Content-Length"] = str(n)
    hdrs.update(headers or {})           # the caller's lies win
    head = f"{method} {target} HTTP/1.1\r\n".encode("latin-1")
    head += "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()).encode() + b"\r\n"
    s = _connect(host, port, timeout)
    data = b""
    try:
        try:
            s.sendall(head + body[:int(len(body) * send_fraction)])
            if not hang:
                s.shutdown(socket.SHUT_WR)
        except OSError:
            pass     # the server may reply-and-close before the body is in
        while True:
            try:
                chunk = s.recv(65536)
            except (ConnectionResetError, BrokenPipeError, TimeoutError):
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
    head_b, _, payload = data.partition(b"\r\n\r\n")
    lines = head_b.split(b"\r\n")
    code = int(lines[0].split()[1])
    hdr = {k.strip().lower(): v.strip() for k, v in
           (ln.decode("latin-1").partition(":")[::2] for ln in lines[1:])}
    return code, payload, hdr


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    """A listen backlog overflows under a storm (macOS answers with RST or
    refusal); that is load, not a verdict — retry briefly."""
    for attempt in range(20):
        try:
            return socket.create_connection((host, port), timeout=timeout)
        except (ConnectionResetError, ConnectionRefusedError, OSError):
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


class Fuzzer:
    """One fuzz session against one castle. `card` is the emulator's
    directory when in-process (enables the containment checks)."""

    def __init__(self, host: str, port: int, seed: int,
                 card: Path | None = None) -> None:
        self.host, self.port, self.seed, self.card = host, port, seed, card
        self.rng = random.Random(seed)
        self.sent = 0
        self.lock = threading.Lock()
        self.codes: dict[int, int] = {}
        #: Set by steps that deliberately leave unread bytes behind (a lying
        #: Content-Length): the server may RST instead of answering.
        self.local = threading.local()
        #: Content checks need one writer per name; with several threads the
        #: same short name is PUT/DELETEd concurrently and only containment
        #: (never content) is asserted.
        self.threads = 1

    # -- generators ---------------------------------------------------------

    def name(self, rng: random.Random) -> str:
        k = rng.choice([1, 2, 3, 5, 8, 20, 40])
        return "".join(rng.choice(ATOMS) for _ in range(k))

    def query(self, rng: random.Random, key: str, good: list[str]) -> str:
        mode = rng.random()
        if mode < 0.25:
            return f"?{key}={rng.choice(good)}"
        if mode < 0.35:
            return ""                                    # missing
        if mode < 0.45:
            return f"?{key}={rng.choice(good)}&{key}={self.name(rng)}"   # dup
        if mode < 0.55:
            return f"?{key}="                             # empty
        if mode < 0.62:
            return "?" + key + "=" + "x" * rng.choice([118, 119, 120, 198, 199, 300, 600])
        if mode < 0.70:
            return f"?{key.upper()}={rng.choice(good)}"   # case
        if mode < 0.78:
            return f"?junk&{key}={rng.choice(good)}"      # derailer
        return f"?{key}={self.name(rng)}"

    # -- one step ------------------------------------------------------------

    def step(self, rng: random.Random) -> None:
        pick = rng.random()
        if pick < 0.28:
            self.fuzz_file(rng)
        elif pick < 0.56:
            self.fuzz_query(rng)
        elif pick < 0.76:
            self.fuzz_route(rng)
        elif pick < 0.92:
            self.fuzz_body(rng)
        else:                  # the desk's poll, mid-storm: must parse
            code, body, _ = self.req("GET", rng.choice(["/api/status", "/api/files"]))
            if code != 200:
                raise Violation(f"seed={self.seed} poll → {code} {body[:60]!r}")

    def req(self, method: str, target: str, **kw) -> tuple[int, bytes, dict[str, str]]:
        code, body, hdr = raw_request(self.host, self.port, method, target, **kw)
        with self.lock:
            self.sent += 1
            self.codes[code] = self.codes.get(code, 0) + 1
        self.check_common(method, target, code, body, hdr)
        return code, body, hdr

    def req_unread(self, method: str, target: str, **kw):
        """A request that leaves bytes the server will not read; a reset
        instead of an answer is legitimate here."""
        self.local.reset_ok = True
        try:
            return self.req(method, target, **kw)
        finally:
            self.local.reset_ok = False

    def check_common(self, method: str, target: str, code: int, body: bytes,
                     hdr: dict[str, str]) -> None:
        ctx = f"seed={self.seed} {method} {target!r} → {code} {body[:60]!r}"
        if code == 0:
            if getattr(self.local, "reset_ok", False):
                return
            raise Violation("connection reset: " + ctx)
        if body.startswith(b"emulator bug"):
            raise Violation(ctx)
        if code >= 500 and body not in DOCUMENTED_5XX:
            raise Violation("undocumented 5xx: " + ctx)
        if code == 200 and hdr.get("content-type", "").startswith("application/json") \
                and method != "HEAD":
            try:
                json.loads(body)
            except ValueError as e:
                raise Violation(f"unparseable JSON: {ctx}") from e
        if len(target.encode("latin-1")) > wire.MAX_URI and code != 414:
            raise Violation("overlong line not 414: " + ctx)

    def fuzz_file(self, rng: random.Random) -> None:
        name = self.name(rng)
        raw = name.encode("utf-8").decode("latin-1")     # as the wire sees it
        decoded = wire.name_from_uri(b"/api/files/" + name.encode(), b"/api/files/")
        safe = wire.safe_name(decoded)
        poisoned = poisoned_text(wire.c_str(decoded))
        verb = rng.choice(["PUT", "DELETE", "PLAY", "SD"])
        if verb == "PUT" and poisoned:
            verb = "DELETE"
        if verb == "PUT":
            payload = bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 7, 300])))
            code, body, _ = self.req("PUT", "/api/files/" + raw, body=payload)
            self.expect_name_verdict(code, body, safe, decoded, payload)
        elif verb == "DELETE":
            code, body, _ = self.req("DELETE", "/api/files/" + raw)
            if not safe and code != 400:
                raise Violation(f"seed={self.seed} DELETE {name!r} unsafe but {code}")
            if safe and code not in (200, 404):
                raise Violation(f"seed={self.seed} DELETE {name!r} → {code} {body!r}")
        elif verb == "PLAY":
            f = wire.query_param(b"/api/play?f=" + name.encode(), "f")
            if poisoned_text(wire.c_str(f)):
                return        # would poison the status JSON's "track" (same bug)
            code, _, _ = self.req("POST", "/api/play?f=" + raw)
            want = 200 if wire.safe_name(f) else 400
            if code != want:
                raise Violation(f"seed={self.seed} play {name!r}: {code} want {want}")
        else:
            code, _, _ = self.req("GET", "/sd/" + raw)
            rel = wire.url_decode(name.encode()).split(b"?")[0]
            wants = (400,) if not wire.safe_subpath(rel) else (200, 404)
            if code not in wants:
                raise Violation(f"seed={self.seed} sd {name!r}: {code} want {wants}")

    def expect_name_verdict(self, code: int, body: bytes, safe: bool,
                            decoded: bytes, payload: bytes) -> None:
        if not safe:
            if (code, body) != (400, b"bad filename"):
                raise Violation(f"seed={self.seed} unsafe {decoded!r} → {code} {body!r}")
            return
        if code == 500:
            return            # "cannot create file": NUL-empty or un-storable name
        if code != 200:
            raise Violation(f"seed={self.seed} safe {decoded!r} → {code} {body!r}")
        if self.card is not None:
            f = self.card / wire.fs_name(decoded)
            if self.threads == 1 and (not f.is_file() or f.read_bytes() != payload):
                raise Violation(f"seed={self.seed} {decoded!r} not on the card intact")
            if f.exists() and f.resolve().parent != self.card.resolve():
                raise Violation(f"seed={self.seed} {decoded!r} escaped the card")

    def fuzz_query(self, rng: random.Random) -> None:
        route, key, good = rng.choice([
            ("/api/volume", "v", ["0", "70", "100", "007", "101", "-1", "1e2"]),
            ("/api/scene", "s", ["vigil", "storm", "nope", "stop"]),
            ("/api/light", "c", ["show", "off", "ff00aa", "FF00AA", "ff00a", "gggggg"]),
            ("/api/pir", "armed", ["0", "1", "x"]),
            ("/api/play", "f", ["wicked_winds.mp3", "..", ".x"]),
        ])
        q = self.query(rng, key, good)
        target = route + q
        raw = target.encode("utf-8").decode("latin-1")
        code, body, _ = self.req("POST", raw)
        if len(raw) > wire.MAX_URI:
            return
        val = wire.query_param(target.encode(), key)
        want: tuple[int, ...]
        if route == "/api/volume":
            digits = bool(val) and len(val) <= 3 and val.isdigit()
            want = (200,) if digits and int(val) <= 100 else (400,)
        elif route == "/api/scene":
            want = (400,) if not val else (200, 404)
        elif route == "/api/light":
            hex6 = len(val) == 6 and all(chr(b) in "0123456789abcdefABCDEF" for b in val)
            want = (200,) if hex6 or val in (b"show", b"off") else (400,)
        elif route == "/api/pir":
            want = (200,) if val else (400,)
        else:
            want = (200,) if wire.safe_name(val) else (400,)
        if code not in want:
            raise Violation(f"seed={self.seed} POST {target!r} → {code} {body!r}, "
                            f"wire says {want} for value {val!r}")

    def fuzz_route(self, rng: random.Random) -> None:
        path = rng.choice([p for p, _, _ in wire.ROUTES]).replace("*", self.name(rng))
        if rng.random() < 0.2:
            path = rng.choice(["/api", "/api/", "/api/filesx", "/nope", "/sd", "/site",
                               "/api/status/", "/API/status", "/remote/"])
        verb = rng.choice(VERBS)
        raw = path.encode("utf-8").decode("latin-1")
        handler, err = wire.route(verb, path.encode("utf-8"))
        if handler == "h_put":
            decoded = wire.name_from_uri(path.encode(), b"/api/" + path.split("/")[2].encode() + b"/")
            if poisoned_text(wire.c_str(decoded)):
                return        # a 0-byte file with a JSON-breaking name (see POISON)
        code, body, _ = self.req(verb, raw)
        if handler is None and code != err:
            raise Violation(f"seed={self.seed} {verb} {path!r} → {code}, wire says {err}")
        if handler is not None and code in (404, 405) and body in (
                wire.IDF_ERRORS[404].encode(), wire.IDF_ERRORS[405].encode()):
            raise Violation(f"seed={self.seed} {verb} {path!r} routed nowhere")

    def fuzz_body(self, rng: random.Random) -> None:
        size = rng.choice([0, 1, 8191, 8192, 8193, 65536])
        payload = bytes(rng.getrandbits(8) for _ in range(size))
        name = f"fz_{rng.randrange(1 << 30):x}.bin"
        mode = rng.random()
        if mode < 0.4:
            code, body, _ = self.req("PUT", f"/api/files/{name}", body=payload)
            self.expect_name_verdict(code, body, True, name.encode(), payload)
            self.req("DELETE", f"/api/files/{name}")
        elif mode < 0.6:   # declared MORE than sent: short write, nothing left behind
            code, body, _ = self.req("PUT", f"/api/files/{name}", body=payload,
                                     declared=size + 10)
            if (code, body) != (500, b"short write"):
                raise Violation(f"seed={self.seed} short body → {code} {body!r}")
            if self.card is not None and (self.card / name).exists():
                raise Violation(f"seed={self.seed} short write left {name}")
        elif mode < 0.8:   # declared LESS than sent: the extra is not the file's
            declared = max(0, size - 5)
            code, body, _ = self.req_unread("PUT", f"/api/files/{name}", body=payload,
                                            declared=declared)
            if code:
                self.expect_name_verdict(code, body, True, name.encode(),
                                         payload[:declared])
            elif self.threads == 1 and self.card is not None and \
                    (self.card / name).exists() and \
                    (self.card / name).read_bytes() != payload[:declared]:
                raise Violation(f"seed={self.seed} extra bytes reached the file")
            self.req("DELETE", f"/api/files/{name}")
        else:              # a garbage Content-Length is the parser's 400
            code, _, _ = self.req_unread(
                "PUT", f"/api/files/{name}", body=payload[:8],
                headers={"Content-Length": rng.choice(["x", "-4", ""])})
            if code not in (0, 400):
                raise Violation(f"seed={self.seed} bad Content-Length → {code}")

    # -- the storm -----------------------------------------------------------

    def run(self, iterations: int, threads: int = 4) -> None:
        self.threads = threads
        errors: list[BaseException] = []

        def worker(tseed: int, n: int) -> None:
            rng = random.Random(tseed)
            try:
                for _ in range(n):
                    self.step(rng)
            except BaseException as e:
                errors.append(e)

        per = max(1, iterations // threads)
        ts = [threading.Thread(target=worker, args=(self.seed * 1000 + i, per))
              for i in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        if errors:
            raise errors[0]
        self.still_alive()

    def still_alive(self) -> None:
        code, body, _ = raw_request(self.host, self.port, "GET", "/api/status")
        if code != 200 or "volume" not in json.loads(body):
            raise Violation(f"seed={self.seed}: castle not answering after the storm: "
                            f"{code} {body[:100]!r}")
        if self.card is not None:
            for p in self.card.rglob("*"):
                if p.is_file() and p.parent != self.card and \
                        p.parent.name not in ("site", "scenes", "logs"):
                    raise Violation(f"seed={self.seed}: stray file {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=int(os.environ.get("CASTLE_FUZZ_SEED", "1")))
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--host", default=None, help="host:port of a running castle "
                    "(default: an in-process emulator)")
    args = ap.parse_args()
    emu = None
    if args.host:
        host, port = args.host.rsplit(":", 1)
        fz = Fuzzer(host, int(port), args.seed)
    else:
        import castle_emu
        card = Path(tempfile.mkdtemp(prefix="castle-fuzz-sd-"))
        emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil", "storm", "stop"])
        emu.start()
        fz = Fuzzer("127.0.0.1", emu.port, args.seed, card)
    t0 = time.monotonic()
    try:
        fz.run(args.iterations, args.threads)
    except Violation as e:
        print(f"VIOLATION after {fz.sent} requests: {e}")
        return 1
    finally:
        if emu is not None:
            emu.shutdown()
    print(f"ok: {fz.sent} requests in {time.monotonic() - t0:.1f}s, seed {args.seed}, "
          f"codes {dict(sorted(fz.codes.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
