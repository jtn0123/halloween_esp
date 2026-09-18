"""h_ota's ceiling is the build's app partition, not one number.

sd_web_ota.h compares the upload against `part->size`; the Feather's 4 MB
layout gives 1.75 MB slots and the WROOM carrier's 8 MB gives 3.75 MB. The
emulator used to carry the smaller number alone, so a carrier image over
1.75 MB was refused as "implausible" by the rehearsal and would have been
accepted by the board (grade report 2026-09-06 J5). The keys were "s2"/"s3"
until the S2 was retired (2026-09-17) — both builds are the same chip now and
what differs is the flash, so they are named after the boards.
"""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu
import castle_emu_http


def put_ota(port: int, image: bytes, send_body: bool) -> int:
    """PUT /api/ota by hand. The board answers a refused size from the
    Content-Length alone, before a byte of body, and hangs up — so the
    refusing case reads the status without sending the body (urllib would
    die of EPIPE mid-upload), and the accepting case sends it all."""
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    with s:
        s.sendall(
            b"PUT /api/ota HTTP/1.1\r\nHost: castle\r\n"
            + b"Content-Length: %d\r\n\r\n" % len(image)
        )
        if send_body:
            s.sendall(image)
        status = s.recv(64).split(b" ")[1]
    return int(status)


class TestOtaSlot(unittest.TestCase):
    def test_the_carrier_slot_takes_what_the_feather_refuses(self) -> None:
        # 2 MB: over the Feather's slot, under the carrier's.
        image = b"\xe9" + b"\0" * (2 * 1024 * 1024)
        for board, want in (("feather", 400), ("carrier", 200)):
            emu = castle_emu.CastleEmu(
                port=0, ota_slot=castle_emu_http.OTA_SLOTS[board]
            )
            emu.start()
            try:
                self.assertEqual(put_ota(emu.port, image, want == 200), want, board)
            finally:
                emu.shutdown()
                emu.server_close()

    def test_the_default_is_the_castle_in_the_yard(self) -> None:
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        self.assertEqual(emu.ota_slot, castle_emu_http.OTA_SLOTS["feather"])
        self.assertEqual(castle_emu_http.OTA_SLOTS["feather"], 0x1C0000)
        self.assertEqual(castle_emu_http.OTA_SLOTS["carrier"], 0x3C0000)
        self.assertEqual(sorted(castle_emu_http.OTA_SLOTS), ["carrier", "feather"])


if __name__ == "__main__":
    unittest.main()
