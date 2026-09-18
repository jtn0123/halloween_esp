"""Where a URL import may reach — and where it may not.

The studio hands a pasted link to yt-dlp and ffmpeg, which will fetch any
http(s) target on the host's behalf: the router's admin page, the castle,
a neighbour's printer. Nothing malicious is expected on a porch network,
but a LAN visitor poking at `--lan` should not get a free proxy into
addresses only the studio's machine can see. So: a target that resolves
to a private, loopback, link-local or otherwise non-public address is
refused — unless the caller IS the studio's own machine (127.0.0.1), who
can already reach those addresses without us.

The rules, in full, because both twins must state the same ones
(`core/src/netguard.rs`, and docs/PARITY.md holds the gate):

- **Loopback callers are exempt, before anything else is looked at** —
  no host parse, no DNS, no classification. They can reach the LAN
  without us, so there is nothing to protect.
- **A non-loopback caller is refused unless EVERY resolved address is
  public.** Every, not any: split-horizon and rebinding DNS answer with
  both faces, and the private one decides.
- **A name that resolves to NOTHING is refused too** (fail closed).
  There used to be an allow here, on the reasoning that yt-dlp phrases a
  lookup failure better than we do — but "no answer" and "no answer
  *yet*" are the same bytes, so a name that resolves on the second try
  (a slow or rebinding server, a resolver that just came up) walked
  straight through the guard. A caller who can already reach the LAN
  loses nothing by the refusal; one who cannot must not be handed a
  retry loop.
- **v4-mapped (`::ffff:0:0/96`) and NAT64 (`64:ff9b::/96`) addresses are
  judged by the IPv4 they carry**, because that is the address the fetch
  will actually land on. `64:ff9b:1::/48`, the local-use NAT64 prefix, is
  not that — it stays non-public whatever it wraps.
- Everything else is CPython's `ipaddress` classification, which
  `netguard.rs` re-implements range by range rather than approximating.

**Known limit: this checks the FIRST hop only.** The host in the pasted
URL is resolved and classified; a 30x redirect yt-dlp follows to a
private address is not seen, and neither is a resolution that changes
between this check and the fetch (DNS rebinding proper). Closing either
means putting the guard inside the fetcher rather than in front of it.

Defence in depth only; the accepted position is that the studio is a
local-only tool (docs/SECURITY.md).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: NAT64 (RFC 6052): the low 32 bits are the IPv4 the packet reaches.
NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


def is_loopback(ip: str) -> bool:
    """Is this client address the studio's own machine?"""
    try:
        return ipaddress.ip_address(ip.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def embedded_v4(addr: IPAddress) -> IPAddress:
    """The IPv4 this address really reaches, when it wraps one: a v4-mapped
    form or a NAT64 (`64:ff9b::/96`) translation. Anything else is itself."""
    if not isinstance(addr, ipaddress.IPv6Address):
        return addr
    if addr.ipv4_mapped:
        return addr.ipv4_mapped
    if addr in NAT64:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def is_public(addr: IPAddress) -> bool:
    """Globally routable, and nothing else: not private/loopback/link-local/
    multicast/reserved/unspecified. `is_global` already says most of that;
    the explicit checks keep the intent readable and cover the versions
    where it lagged (e.g. 0.0.0.0, the v6-mapped forms)."""
    addr = embedded_v4(addr)
    return addr.is_global and not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def resolve(host: str) -> list[IPAddress]:
    """Every address the name stands for; an IP literal is itself. An
    unresolvable name is an empty list — which `refuse_reason` treats as a
    refusal, not as a pass."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (UnicodeError, OSError):  # gaierror is an OSError
        return []
    out: list[IPAddress] = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    return out


def refuse_reason(url: str, client_ip: str) -> str | None:
    """Why this caller may not fetch this URL — or None if they may.

    A loopback caller may fetch anything. Anyone else may only fetch hosts
    whose EVERY resolved address is public; a name with no host at all, one
    that resolves into a private range, and one that does not resolve at
    all are refused with a reason the page can show.
    """
    if is_loopback(client_ip):
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return "that link has no usable host"
    if not host:
        return "that link has no host"
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(
        ".local"
    ):
        return f"{host} is not a public address"
    addrs = resolve(host)
    if not addrs:
        return f"{host} does not resolve — only the studio's own machine may fetch a name this one cannot look up"
    bad = [str(a) for a in addrs if not is_public(a)]
    if bad:
        return f"{host} is not a public address ({bad[0]}) — only the studio's own machine may fetch from the LAN"
    return None
