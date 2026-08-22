"""Where a URL import may reach — and where it may not.

The studio hands a pasted link to yt-dlp and ffmpeg, which will fetch any
http(s) target on the host's behalf: the router's admin page, the castle,
a neighbour's printer. Nothing malicious is expected on a porch network,
but a LAN visitor poking at `--lan` should not get a free proxy into
addresses only the studio's machine can see. So: a target that resolves
to a private, loopback, link-local or otherwise non-public address is
refused — unless the caller IS the studio's own machine (127.0.0.1), who
can already reach those addresses without us.

Defence in depth only; the accepted position is that the studio is a
local-only tool (docs/SECURITY.md).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def is_loopback(ip: str) -> bool:
    """Is this client address the studio's own machine?"""
    try:
        return ipaddress.ip_address(ip.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def is_public(addr: IPAddress) -> bool:
    """Globally routable, and nothing else: not private/loopback/link-local/
    multicast/reserved/unspecified. `is_global` already says most of that;
    the explicit checks keep the intent readable and cover the versions
    where it lagged (e.g. 0.0.0.0, the v6-mapped forms)."""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_private or addr.is_loopback
                                   or addr.is_link_local or addr.is_multicast
                                   or addr.is_reserved or addr.is_unspecified)


def resolve(host: str) -> list[IPAddress]:
    """Every address the name stands for; an IP literal is itself. An
    unresolvable name is an empty list (yt-dlp will say so better)."""
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (UnicodeError, OSError):        # gaierror is an OSError
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
    whose EVERY resolved address is public; a name with no host at all, or
    that resolves into a private range, is refused with a reason the page
    can show.
    """
    if is_loopback(client_ip):
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return "that link has no usable host"
    if not host:
        return "that link has no host"
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return f"{host} is not a public address"
    addrs = resolve(host)
    bad = [str(a) for a in addrs if not is_public(a)]
    if bad:
        return f"{host} is not a public address ({bad[0]}) — only the studio's own machine may fetch from the LAN"
    return None
