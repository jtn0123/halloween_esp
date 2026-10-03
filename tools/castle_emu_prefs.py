"""The emulated castle's owner settings — sd_web_prefs.h's three routes.

POST /api/settings, /api/key and /api/factory-reset, every one behind the
castle key when one is set. Split out of castle_emu_http.py in v5.75, when
/api/settings grew from one setting (boot_play, v5.74) to four — the owner's
timezone, volume cap and quiet hours joined it (castle_emu_owner.py is their
arithmetic) — and the handlers file reached the 500-line cap. The firmware
keeps them in a header of their own for the same reason.

The board keeps all of it in NVS; the emulator keeps it for as long as it
runs, which is one boot. tests/test_firmware_contract.py reads this file
beside the other handler files, so each reply_err string here is held to
the C exactly as it would be next door.
"""

from __future__ import annotations

import castle_emu_owner as owner
import castle_emu_tz as tz
import castle_emu_wire as wire
from castle_emu_reply import JSON_MIME, QUERY_TOO_LONG
from castle_emu_upload import Uploads


class Prefs(Uploads):
    """The owner's three routes. Mixed into castle_emu_http.Handler."""

    def h_settings(self, raw: bytes) -> None:
        """sd_web_prefs.h h_settings: any subset of boot_play=, tz=,
        vol_max= and quiet=, each checked in the C's order, all saved or
        none, and the whole settings object back."""
        if not self._key_ok():
            return self._locked()
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        bp, zone, vm, quiet = (
            wire.query_param(raw, k) for k in ("boot_play", "tz", "vol_max", "quiet")
        )
        if not (bp or zone or vm or quiet):
            return self._err(400, "need boot_play=, tz=, vol_max= or quiet=")
        ok, bp = wire.pir_armed_ok(bp)
        if not ok:
            return self._err(400, "bad boot_play")
        if zone and not tz.tz_ok(zone):
            return self._err(400, "bad tz")
        cap = owner.vol_max_ok(vm) if vm else None
        if vm and cap is None:
            return self._err(400, "bad vol_max")
        window = owner.quiet_ok(quiet) if quiet else None
        if quiet and window is None:
            return self._err(400, "bad quiet")
        emu = self.server
        if bp:
            emu.boot_play = bp == b"1"
        if zone:
            emu.owner.set_tz(zone.decode("ascii"))
        if cap is not None:
            emu.owner.vol_max = cap
        if window is not None:
            emu.owner.quiet = window
        self._raw(200, emu.owner.settings_json(emu.boot_play), JSON_MIME)

    def h_key(self, raw: bytes) -> None:
        if not self._key_ok():
            return self._locked()
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        nk = wire.query_param(raw, "new")
        clear = wire.query_param(raw, "clear") == b"1"
        if (not nk) == (not clear):
            return self._err(400, "need new=<key> or clear=1")
        if not clear and not wire.key_chars_ok(nk):
            return self._err(400, "bad key")
        self.server.key = b"" if clear else nk
        self._raw(
            200,
            b'{"locked":false}' if clear else b'{"locked":true}',
            JSON_MIME,
        )

    def h_factory_reset(self, raw: bytes) -> None:
        """The board erases NVS and reboots after this reply; the emulator
        forgets its settings — the key, boot_play, and since v5.75 the
        owner's zone, cap and window — which is everything a client can
        observe."""
        if not self._key_ok():
            return self._locked()
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        if wire.query_param(raw, "confirm") != b"yes":
            return self._err(400, "need confirm=yes")
        self.server.key = b""
        self.server.boot_play = self.server.boot_play_default
        self.server.owner.reset()
        self._raw(200, b'{"resetting":true}', JSON_MIME)
