"""What `/api/status` says — the emulator's h_status, twice over.

Split out of castle_emu.py at the 500-line cap, on the seam the firmware
has: castle_emu.py is the castle's STATE (the card, the mirrored show, the
one-slot mailbox and its 200 ms tick) and this is the one REPLY built from
it. `sd_web.h` draws the same line — `h_status` formats, nothing else.

Two functions because the firmware has two things to be held to and they are
not the same thing:

  status_json   the reply as data, which is what every Python caller and the
                desk read, and what the tests compare field by field
  status_text   the reply as BYTES, through h_status's own printf template
                and `json_escape` — because a '"' in a track name or in the
                missing list has to go out escaped on both castles, and only
                a string comparison can show that it does
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING

import castle_emu_wire as wire
from castle_emu_clock import audio_state

if TYPE_CHECKING:  # the state half; imported for typing only, so no cycle
    from castle_emu import CastleEmu


def status_json(emu: CastleEmu) -> dict[str, object]:
    st = emu.state
    # Real numbers from the disk under the card dir — the point is that
    # the field EXISTS and is honest, same as v5.23's esp_vfs_fat_info.
    du = shutil.disk_usage(emu.sd_dir)
    now = time.monotonic()
    with st.lock:
        playing, position_ms = audio_state(
            st.track, st.track_started, st.track_ends, st.starting_until, now
        )
        return {
            "version": emu.version,
            "compiled": "emulated",
            "uptime_s": int(time.monotonic() - st.boot),
            "sd_mounted": emu.sd_mounted,
            "psram_free_kb": 1800,
            "heap_free_kb": 96,
            "sd_total_kb": du.total // 1024 if emu.sd_mounted else 0,
            "sd_free_kb": du.free // 1024 if emu.sd_mounted else 0,
            "missing": emu.missing,
            "volume": st.volume,
            "scene": st.scene,
            "track": st.track,
            # B1: the ids this "build" runs with — the same list
            # /api/scene checks, so the desk can spot a stale board.
            "scenes": ",".join(emu.scenes),
            "show_on": st.show_on,
            # v5.52/5.55: the speaker's word, not the mailbox's, and a
            # clock that counts from the sound (castle_emu_clock).
            "playing": playing,
            "position_ms": position_ms,
            # v5.59: LIGHT frames the main loop ran, and the ones the
            # one-slot mailbox dropped before it could (sd_web_state.h).
            "light_applied": emu.events.light_applied,
            "light_evicted": emu.events.light_evicted,
            "cues": st.cues,
            # L2 (v5.62): unix seconds, or 0 before SNTP answers. The
            # ring stamps uptime and always will; this is the base a
            # page turns one into the other with. An emulator always
            # has a clock, so it is never the 0 case — the KEY is the
            # contract, and a desk that converts must find it on both.
            "epoch": int(time.time()),
            # L6: the radio, in dBm. A fixed plausible reading here for
            # the same reason psram_free_kb is fixed: the number means
            # nothing off the board, the key means everything.
            "rssi": -55,
            "pir": {
                "armed": st.pir["armed"],
                "cooldown_s": st.pir["cooldown_s"],
                "scene": st.pir["scene"],
            },
        }


def status_text(emu: CastleEmu) -> str:
    """h_status's template: numbers through the same formats, strings
    through json_escape — the C mirrors Python's json.dumps table, so a
    '"' in the track or the missing list goes out escaped on both."""
    s = status_json(emu)
    pir = s["pir"]
    assert isinstance(pir, dict)
    b = {True: "true", False: "false"}

    def i(k: str) -> int:
        return int(str(s[k]))

    def t(k: str) -> str:
        return wire.json_escape(str(s[k]))

    return (
        '{"version":"%s","compiled":"%s","uptime_s":%d,'
        '"sd_mounted":%s,"psram_free_kb":%d,"heap_free_kb":%d,'
        '"sd_total_kb":%d,"sd_free_kb":%d,"missing":"%s",'
        '"volume":%d,"scene":"%s","track":"%s","scenes":"%s",'
        '"show_on":%s,"playing":%s,"position_ms":%d,'
        '"light_applied":%d,"light_evicted":%d,"cues":%d,"epoch":%d,"rssi":%d,'
        '"pir":{"armed":%s,"cooldown_s":%d,"scene":"%s"}}'
        % (
            t("version"),
            t("compiled"),
            i("uptime_s"),
            b[bool(s["sd_mounted"])],
            i("psram_free_kb"),
            i("heap_free_kb"),
            i("sd_total_kb"),
            i("sd_free_kb"),
            t("missing"),
            i("volume"),
            t("scene"),
            t("track"),
            t("scenes"),
            b[bool(s["show_on"])],
            b[bool(s["playing"])],
            i("position_ms"),
            i("light_applied"),
            i("light_evicted"),
            i("cues"),
            i("epoch"),
            i("rssi"),
            b[bool(pir["armed"])],
            int(pir["cooldown_s"]),
            wire.json_escape(str(pir["scene"])),
        )
    )
