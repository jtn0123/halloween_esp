"""A client that hangs up mid-reply is the client's verdict, not an emulator bug.

castle_link gives a POST two seconds of read budget. When the emulated
castle is parked - the serial-mode long PUT, or the pre-v5.22 wedge - the
bridge reports 504 "may have landed" and closes its socket, exactly as
tests/test_emu_modes.py asserts. The parked request then runs to completion
and writes its reply into a socket nobody holds. Before 2026-09-06 that
printed two tracebacks into every `make check`: the handler's EPIPE, then
socketserver's for the 500 the dispatcher tried to send down the same dead
socket. The real httpd's send just fails and the handler returns; the
emulator does the same now, and this pins it.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import socket
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_emu


class TestClientHangsUp(unittest.TestCase):
    def test_a_reply_into_a_closed_socket_prints_nothing(self) -> None:
        emu = castle_emu.CastleEmu(port=0, wedge=True)
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        port = emu.server_address[1]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # "A track is playing": the wedge parks every request AFTER it
            # has been parsed, which is where the serial PUT parks it too.
            with emu.state.lock:
                emu.state.track = "spin.mp3"
                emu.state.track_ends = time.monotonic() + 60
            sock = socket.create_connection(("127.0.0.1", port))
            sock.sendall(
                b"POST /api/stop HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
            )
            time.sleep(0.3)  # the handler is spinning in _wedge now
            # An abortive close: RST on the wire at once, so the handler's
            # first write fails rather than the kernel buffering it.
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            sock.close()
            time.sleep(0.2)
            with emu.state.lock:
                emu.state.track = ""  # the song ends; the reply goes out
            time.sleep(0.5)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/api/status")
            self.assertEqual(conn.getresponse().status, 200)
            conn.close()
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
