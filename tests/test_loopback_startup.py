"""Loopback servers start without macOS 15's slow reverse DNS lookup."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from castle_emu import CastleEmu


class TestLoopbackStartup(unittest.TestCase):
    def test_emulator_answers_without_resolving_its_loopback_address(self) -> None:
        with (
            tempfile.TemporaryDirectory() as card,
            mock.patch(
                "socket.getfqdn",
                side_effect=AssertionError("reverse DNS is unavailable"),
            ) as resolve,
        ):
            emu = CastleEmu(port=0, sd_dir=Path(card), scenes=["vigil"])
            try:
                emu.start()
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{emu.port}/api/status", timeout=2
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIsInstance(json.load(response), dict)
                    self.assertEqual(
                        (emu.server_name, emu.server_port), emu.server_address
                    )
                    resolve.assert_not_called()
                finally:
                    emu.shutdown()
            finally:
                emu.server_close()


if __name__ == "__main__":
    unittest.main()
