"""The emulated castle's event ring and light counters — the Python half of
firmware/sd_web_state.h's ring and firmware/sd_web_events.h's rendering.

The board records what its MAIN LOOP did: one entry per command the 200 ms
interval executed, the audio clock's start and end, and the light frames the
one-slot mailbox dropped on the way in (rate-limited to one line a second, or
a 4 Hz import would push every other event out of a 64-entry ring). Nothing
is written to the card from there: a card write on the main loop stalls the
audio and the pixels, which is the very thing the ring exists to explain.

Split out of castle_emu.py along the firmware's own seam (the ring is its own
header there) and held to the C by tests/test_firmware_contract.py.
"""

from __future__ import annotations

import threading

import castle_emu_wire as wire

#: sd_web_state.h kEventRing: entries kept, oldest dropped first.
RING = 64
#: kEventArgMax - 1: the bytes of an arg that fit beside the NUL. A scene id
#: and a volume fit whole; a long track name is truncated, on both sides.
ARG_MAX = 47
#: note_light_evictions(): at most one light_evicted line per second.
EVICT_GAP_MS = 1000
#: note_reply_error() (sd_web_util.h, L11): at most one http_err line per
#: two seconds, or a client in a retry loop would flush the ring.
ERR_GAP_MS = 2000
#: The kind words that are NOT a drained mailbox action — the ring lines the
#: main loop, the PIR, the buttons and the server itself write. Held to
#: castle_rtc.h's table by tests/test_firmware_contract.py.
OTHER_KINDS = (
    "light_evicted",
    "sound",
    "silent",
    # v5.62 (L3/L6/L11): everything the ring used to miss.
    "scene_start",
    "pir",
    "pir_cooldown",
    "pir_off",
    "button",
    "wifi_up",
    "wifi_down",
    "http_err",
)

#: ActionType → the "e" field of /api/events. LIGHT and PIRCFG are not show
#: events (a LIGHT only bumps the applied counter), so they are absent.
ACTION_KIND = {
    "PLAY": "play",
    "SCENE": "scene",
    "STOP": "stop",
    "VOLUME": "volume",
    "SHOW": "show",
    "BLACKOUT": "blackout",
    "RESTART": "restart",
}


class Events:
    """castle_web's ring and counters, behind one lock like the firmware's."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        #: (uptime_ms, kind, arg, truncated) — the last of those is A12's
        #: marker for an arg that did not fit ARG_MAX.
        self.ring: list[tuple[int, str, str, bool]] = []
        #: LIGHT commands the tick actually ran, and frames the slot dropped.
        self.light_applied = 0
        self.light_evicted = 0
        self._evicted_seen = 0
        self._evict_event_ms = 0
        self._sounding = False
        self._playing = False
        self._err_event_ms = 0
        self._started_ms = 0

    # -- recording (the main loop's side) ----------------------------------

    def record(self, kind: str, arg: str, t_ms: int) -> None:
        raw = arg.encode()
        with self.lock:
            self.ring.append(
                (
                    t_ms,
                    kind,
                    raw[:ARG_MAX].decode("utf-8", "ignore"),
                    len(raw) > ARG_MAX,
                )
            )
            del self.ring[:-RING]

    def record_action(self, action: str, arg: str, t_ms: int) -> None:
        """The tick executed `action`: its line, or the applied counter."""
        if action == "LIGHT":
            with self.lock:
                self.light_applied += 1
            return
        kind = ACTION_KIND.get(action)
        if kind is not None:
            self.record(kind, arg, t_ms)

    def note_light_evictions(self, t_ms: int) -> None:
        """One light_evicted line a second at most, carrying how many frames
        were dropped since the last one."""
        with self.lock:
            dropped = self.light_evicted - self._evicted_seen
            if not dropped:
                return
            if self._evict_event_ms and t_ms - self._evict_event_ms < EVICT_GAP_MS:
                return
            self._evicted_seen = self.light_evicted
            self._evict_event_ms = t_ms
        self.record("light_evicted", str(dropped), t_ms)

    def note_audio(
        self, sounding: bool, playing: bool, t_ms: int, track: str = ""
    ) -> bool:
        """mirror_audio's two transitions: the speaker started (the armed
        clock began running) and playback ended on its own.

        Returns mirror_audio's own return value — true on the tick playback
        ENDED — so the caller can clear a raw track's name the way
        castle_sd_common.yaml does. That is a transition, not a deadline:
        an armed clock whose sound never came holds the name for the whole
        grace, and the emulator used to drop it on the first tick past the
        track's end instead (C4).

        L10 (v5.62): `sound` carries the track the amplifier got and
        `silent` the milliseconds of it that played — an empty arg on both
        made the pair useless for the only question worth asking of them.
        """
        was_sounding, was_playing = self._sounding, self._playing
        self._sounding, self._playing = sounding, playing
        if sounding and not was_sounding:
            self._started_ms = t_ms
            self.record("sound", track, t_ms)
        ended = was_playing and not playing
        if ended:
            played = t_ms - self._started_ms if was_sounding else 0
            self.record("silent", str(max(played, 0)), t_ms)
        return ended

    def note_reply_error(self, code: int, t_ms: int) -> None:
        """L11: one line per refusal, rate-limited exactly as reply_err is."""
        if self._err_event_ms and t_ms - self._err_event_ms < ERR_GAP_MS:
            return
        self._err_event_ms = t_ms
        self.record("http_err", str(code), t_ms)

    # -- reading (the handler's side) --------------------------------------

    def snapshot(self) -> list[tuple[int, str, str, bool]]:
        with self.lock:
            return list(self.ring)

    def json(self) -> str:
        """events_json(): oldest first, the firmware's template exactly."""
        return (
            "["
            + ",".join(
                '{"t":%d,"e":"%s","a":"%s"%s}'
                % (t, kind, wire.json_escape(arg), ',"trunc":true' if trunc else "")
                for t, kind, arg, trunc in self.snapshot()
            )
            + "]"
        )
