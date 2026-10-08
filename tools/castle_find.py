"""Find my castle — a one-shot mDNS browse, then a question each answer can
only pass by being a castle.

A buyer's castle names itself `castle-a1b2c3.local` (castle_buyer.yaml:
`name: castle` + `name_add_mac_suffix`), so nobody can type its name before
it is found, and its router lease is whatever the router chose. This asks
the LAN once, the way RFC 6762 §5.1 allows a simple client to: one query
from an ephemeral port to 224.0.0.251:5353, which every responder answers by
unicast straight back to that port. Asked for, in one packet:

- `_esphomelib._tcp.local` PTR — every ESPHome device, castles included
  (castle.yaml keeps `api:`), whose SRV/A records give its name;
- `_http._tcp.local` PTR — anything with a web page, castles included when
  they advertise one;
- the A record of each `.local` name the store already holds for its first
  castle (hosts.first_castle), so a castle found before is found by name.

Each answer is then asked `GET /api/status` — only an address on this LAN
(private, link-local or loopback: a responder cannot steer the probe out to
the internet) — and kept only if it answers like the firmware does, with
`version` and `scenes`. A printer that advertises `_http._tcp` falls out
there.

Standard library only, on purpose: the buyer's app ships
requirements-desktop.lock, which has no zeroconf (only the dev lock does,
through esphome), and a one-shot query is ~150 lines. Nothing listens on
5353 and nothing is sent anywhere but the group and the answers' own
addresses. Tests aim GROUP at a fake responder on 127.0.0.1
(tests/test_castle_find.py) — a test never multicasts.

    castle_find.py [--json] [--timeout S]
"""

from __future__ import annotations

import ipaddress
import json
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts

#: The mDNS group (RFC 6762). Tests replace it with a fake on loopback.
GROUP = ("224.0.0.251", 5353)
SERVICES = ("_esphomelib._tcp.local", "_http._tcp.local")
_A, _PTR, _SRV = 1, 12, 33
#: QU: "answer me by unicast" (RFC 6762 §5.4), as well as the source port.
_QU = 0x8001
#: When the query goes out again within one browse — a lost packet on Wi-Fi
#: is the common case, not the rare one.
RESEND_AT = (0.0, 0.5, 1.2)


class Found(TypedDict):
    """One castle that answered: where it is and what it said it is."""

    name: str
    address: str
    version: str
    board: str
    fw_variant: str


def _qname(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError(f"not a DNS name: {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def query(names: list[tuple[str, int]]) -> bytes:
    """One mDNS query packet asking every (name, type) in `names`."""
    head = struct.pack("!6H", 0, 0, len(names), 0, 0, 0)
    return head + b"".join(_qname(n) + struct.pack("!2H", t, _QU) for n, t in names)


def _name(buf: bytes, at: int) -> tuple[str, int]:
    """The (possibly compressed) name at `at`, and where the record goes on."""
    labels: list[str] = []
    end = -1
    for _ in range(128):
        if at >= len(buf):
            raise ValueError("truncated name")
        n = buf[at]
        if n & 0xC0 == 0xC0:
            if at + 1 >= len(buf):
                raise ValueError("truncated pointer")
            end = at + 2 if end < 0 else end
            at = ((n & 0x3F) << 8) | buf[at + 1]
            continue
        if n & 0xC0:
            raise ValueError("bad label")
        if n == 0:
            return ".".join(labels), (at + 1 if end < 0 else end)
        labels.append(buf[at + 1 : at + 1 + n].decode("utf-8", "replace"))
        at += 1 + n
    raise ValueError("name loop")


def records(buf: bytes) -> list[tuple[str, int, Any]]:
    """Every A, PTR and SRV record a response carries, as (owner name, type,
    data): an IPv4 string, a name, (port, target). A query, or a packet that
    does not parse, carries none."""
    if len(buf) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack("!6H", buf[:12])
    if not flags & 0x8000:
        return []
    try:
        at = 12
        for _ in range(qd):
            at = _name(buf, at)[1] + 4
        out: list[tuple[str, int, Any]] = []
        for _ in range(an + ns + ar):
            owner, at = _name(buf, at)
            rtype, _cls, _ttl, size = struct.unpack("!2HIH", buf[at : at + 10])
            data_at, at = at + 10, at + 10 + size
            if at > len(buf):
                raise ValueError("truncated record")
            data = _rdata(buf, rtype, data_at, size)
            if data is not None:
                out.append((owner.lower(), rtype, data))
        return out
    except (ValueError, struct.error):
        return []


def _rdata(buf: bytes, rtype: int, at: int, size: int) -> Any:
    if rtype == _A and size == 4:
        return socket.inet_ntoa(buf[at : at + 4])
    if rtype == _PTR:
        return _name(buf, at)[0].lower()
    if rtype == _SRV and size >= 7:
        return struct.unpack("!H", buf[at + 4 : at + 6])[0], _name(buf, at + 6)[
            0
        ].lower()
    return None


def candidates(answers: list[tuple[str, list[tuple[str, int, Any]]]]) -> dict[str, str]:
    """address -> mDNS name, from (source address, records) per answer. An
    A record names the address; failing that, the packet's own source does
    (a unicast answer comes from the responder). An `_http._tcp` service on
    another port than 80 keeps its port; everything else is asked on 80."""
    found: dict[str, str] = {}
    for source, recs in answers:
        a = {owner: ip for owner, t, ip in recs if t == _A}
        srv = {owner: data for owner, t, data in recs if t == _SRV}
        before = len(found)
        for owner, (port, target) in srv.items():
            ip = a.get(target, source)
            web = owner.endswith("._http._tcp.local") and port not in (0, 80)
            found.setdefault(f"{ip}:{port}" if web else ip, target)
        for owner, ip in a.items():
            if not any(addr.split(":")[0] == ip for addr in found):
                found[ip] = owner
        if len(found) == before and not any(
            addr.split(":")[0] == source for addr in found
        ):
            # A bare PTR: the responder is the packet's source, and an
            # ESPHome instance is named after its host.
            ptr = next((d for _, t, d in recs if t == _PTR), "")
            found[source] = f"{ptr.split('.')[0]}.local" if ptr else ""
    return found


def on_this_lan(address: str) -> bool:
    """A probe target the LAN could own: private, link-local or loopback."""
    try:
        ip = ipaddress.ip_address(address.split(":")[0])
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local or ip.is_loopback


def probe(address: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """The castle's /api/status at `address`, or None when what answers
    there is not a castle (or nothing answers)."""
    try:
        with urllib.request.urlopen(
            hosts.castle_url(address, "/api/status"), timeout=timeout
        ) as r:
            state = json.loads(r.read(65536))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(state, dict) and "version" in state and "scenes" in state:
        return state
    return None


def browse(
    timeout: float = 2.0, group: tuple[str, int] | None = None
) -> list[tuple[str, list[tuple[str, int, Any]]]]:
    """Ask once (and again at RESEND_AT), collect answers until `timeout`:
    (source address, records) per packet that parsed as a response."""
    names = [(s, _PTR) for s in SERVICES]
    names += [(h, _A) for h in hosts.first_castle() if h.endswith(".local")]
    packet = query(names)
    to = group or GROUP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    answers: list[tuple[str, list[tuple[str, int, Any]]]] = []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.bind(("127.0.0.1" if to[0].startswith("127.") else "", 0))
        start = time.monotonic()
        sends = list(RESEND_AT)
        sent = 0
        while (now := time.monotonic() - start) < timeout:
            while sends and sends[0] <= now:
                sends.pop(0)
                try:
                    sent += bool(sock.sendto(packet, to))
                except OSError as e:
                    if not sent:  # the first ask failed: there is no network
                        why = e.strerror or str(e)
                        raise OSError(f"could not ask the network: {why}") from None
            wait = min([timeout - now, *(s - now for s in sends)])
            sock.settimeout(max(wait, 0.01))
            try:
                data, (source, _) = sock.recvfrom(9000)
            except (TimeoutError, ConnectionResetError):  # Windows: ICMP
                continue
            if recs := records(data):
                answers.append((source, recs))
    finally:
        sock.close()
    return answers


def find(
    timeout: float = 2.0,
    group: tuple[str, int] | None = None,
    ask: Callable[[str], dict[str, Any] | None] = probe,
) -> list[Found]:
    """Every castle the LAN answers for, by address."""
    found = {
        addr: name
        for addr, name in candidates(browse(timeout, group)).items()
        if on_this_lan(addr)
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = dict(zip(found, pool.map(ask, found), strict=True))
    return sorted(
        (
            Found(
                name=found[addr],
                address=addr,
                version=str(state.get("version", "")),
                board=str(state.get("board", "")),
                fw_variant=str(state.get("fw_variant", "")),
            )
            for addr, state in states.items()
            if state is not None
        ),
        key=lambda f: (f["name"] or "~", f["address"]),
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    timeout = 2.0
    if "--timeout" in args:
        timeout = float(args[args.index("--timeout") + 1])
    castles = find(timeout)
    if "--json" in args:
        print(json.dumps(castles))
        return 0
    for c in castles:
        print(f"{c['address']:<22} {c['name'] or '(no name)':<28} {c['version']}")
    if not castles:
        print("no castle answered — is it on, and on this network?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
