"""What `/api/health` says — the emulator's h_health and castle_health.h's
reset words.

The board counts its boots and crashes in NVS across the season and reads
the reason for THIS boot off the chip (esp_reset_reason). The emulator has
no chip, so all three are given — CastleEmu(boots=, crashes=, reset=), the
same knobs the C harness reads as CASTLE_BOOTS / CASTLE_CRASHES /
CASTLE_RESET — and the owner's page (v5.75) can be shown a brownout without
browning anything out.

REASONS and CRASHES are castle_health.h's reason_str() and was_crash(),
ported: tests/test_firmware_contract.py parses that switch and holds this
table to it entry for entry, so a reason added to one side is a red test
rather than an "unknown" on a page.

The rest is what the board MEASURES, and lives in `emu.health` beside the
knobs, defaulting to what the C harness's shim reports so the two replies
stay byte-identical; tools/soak.py's suite moves them to rehearse a bad
night:
  - sd_read_errors (A8, v5.61) and sd_last_error (L4, v5.62): transfers off
    the card that failed part way, and where the last one did. A host
    directory never NAKs a sector, so on its own the emulated card says 0
    and "".
  - heap_min_kb (L7, v5.62): the low-water mark of internal heap, the
    number that explains a crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from castle_emu_wire import json_escape

if TYPE_CHECKING:
    from castle_emu import CastleEmu

#: esp_reset_reason_t → castle_health::reason_str(). Anything else is
#: "unknown", as on the board.
REASONS = {
    1: "power-on",
    2: "external",
    3: "software",
    4: "PANIC",
    5: "int-watchdog",
    6: "task-watchdog",
    7: "watchdog",
    8: "deep-sleep",
    9: "BROWNOUT",
    10: "sdio",
    11: "usb",
    12: "jtag",
    13: "efuse",
    14: "power-glitch",
    15: "cpu-lockup",
}

#: castle_health::was_crash(): the reasons that mean the firmware or its
#: power fell over, rather than someone switching it on or flashing it.
CRASHES = {"PANIC", "int-watchdog", "task-watchdog", "watchdog", "BROWNOUT",
           "power-glitch", "cpu-lockup"}  # fmt: skip

#: esp_heap_caps.h's shim answer, in KB — see the module docstring.
HEAP_MIN_KB = 64


def reason_word(code: int) -> str:
    return REASONS.get(code, "unknown")


def reason_code(word: str) -> int:
    """reason_word's inverse: the esp_reset_reason_t a test hands the castle
    to have it say `word`. 0 (ESP_RST_UNKNOWN) for a word it never says."""
    return next((c for c, w in REASONS.items() if w == word), 0)


def health_text(emu: CastleEmu) -> str:
    """h_health's template, byte for byte: the knobs, then the readings."""
    word = reason_word(emu.reset_reason)
    h = emu.health
    return (
        '{"boots":%d,"crashes":%d,"last_reset":"%s",'
        '"was_crash":%s,"sd_read_errors":%d,"heap_min_kb":%d,'
        '"sd_last_error":"%s"}'
        % (
            emu.boots,
            emu.crashes,
            word,
            "true" if word in CRASHES else "false",
            int(h["sd_read_errors"]),
            int(h["heap_min_kb"]),
            json_escape(str(h["sd_last_error"])),
        )
    )
