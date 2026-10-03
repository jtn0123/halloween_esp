#!/usr/bin/env python3
"""Castle Radio on a pretend LAN, for tools/guide_shots.py's pictures.

    guide_shots_radio.py EMU_PORT

The real server (demo/castle-radio/server.py) with four things swapped, so
the owner's guide shows what an owner sees and nothing here can reach a
real network:

- every outgoing TCP connection goes through `guard`: the stand-in castle's
  address and name, on port 80, are the emulator on loopback; any other
  loopback port is itself; ANYTHING else is refused. A castle in the yard,
  GitHub and the router are all out of reach by construction;
- Find my castle's mDNS browse answers with one castle, the stand-in;
- the firmware offer is the next version of the one the emulator runs, so
  the update card has something to offer without asking GitHub;
- the demo tracks a developer's checkout may carry in media/ are hidden, as
  an installed app has none — the Listen page is a first run.

The data dir and the castle list come from the parent's environment
(CASTLE_RADIO_DATA, CASTLE_DEVICES — scratch directories it owns). The port
this server took is the first line on stdout.
"""

from __future__ import annotations

import socket
import sys
import tempfile
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RADIO = ROOT / "demo" / "castle-radio"

#: The castle in the pictures: a documented example address (the ship guard
#: allows it) and the name the label tool gives the MAC guide_shots uses.
CASTLE_IP = "192.168.1.50"  # NOSONAR — never dialled: guard() maps it to the emulator
CASTLE_NAME = "castle-a1b2c3"
#: What the update card offers: a release tag a little ahead of the app.
OFFER_TAG = "v0.2.0"

Connect = Callable[..., socket.socket]


def guard(real: Connect, emu_port: int) -> Connect:
    """socket.create_connection that knows only loopback and the stand-in."""
    names = {CASTLE_IP, CASTLE_NAME + ".local", CASTLE_NAME}

    def connect(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        host, port = address[0], int(address[1])
        if host in names and port == 80:
            return real(("127.0.0.1", emu_port), *args, **kwargs)
        if host in ("127.0.0.1", "localhost"):
            return real((host, port), *args, **kwargs)
        raise ConnectionRefusedError(
            f"guide shots: no network beyond loopback ({host})"
        )

    return connect


def answers() -> list[tuple[str, list[tuple[str, int, Any]]]]:
    """One castle on the LAN, as castle_find.browse would hear ESPHome say it."""
    instance = f"{CASTLE_NAME}._esphomelib._tcp.local"
    local = f"{CASTLE_NAME}.local"
    return [
        (
            CASTLE_IP,
            [
                ("_esphomelib._tcp.local", 12, instance),
                (instance, 33, (6053, local)),
                (local, 1, CASTLE_IP),
            ],
        )
    ]


def next_version(version: str) -> str:
    major, minor = version.split(".")[:2]
    return f"{major}.{int(minor) + 1}"


def install(emu_port: int) -> type:
    """Make the swaps, then hand back the server's request handler."""
    socket.create_connection = guard(socket.create_connection, emu_port)
    sys.path.insert(0, str(RADIO))
    import radio_env  # noqa: F401 — the radio's sandbox before any tools/ module

    # isort: split
    import castle_find
    import castle_update
    import castle_update_routes
    import desktop_release
    import first_run
    import fw_formats
    import server

    castle_find.browse = lambda timeout=2.0, group=None: answers()
    offer = castle_update.Offer(
        desktop_release.Release(tag=OFFER_TAG),
        {"version": next_version(fw_formats.this_firmware()), "fw_variant": "buyer"},
    )
    castle_update_routes.offer_for = lambda board, prerelease: offer
    first_run.MEDIA = Path(tempfile.mkdtemp(prefix="guide-shots-media-"))
    return server.Handler


def main(argv: list[str]) -> int:
    handler = install(int(argv[0]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    print(httpd.server_address[1], flush=True)
    httpd.serve_forever()  # NOSONAR — loopback only, for the browser taking pictures
    return 0


if __name__ == "__main__":  # pragma: no cover - a child of guide_shots.py
    sys.exit(main(sys.argv[1:]))
