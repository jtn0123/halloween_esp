"""The emulated castle's audio clock — firmware/sd_web_state.h in Python.

/api/status's `playing` and `position_ms` are not "is a track named": the
board reports the SPEAKER. The pipeline says PLAYING about half a second
before the first sample reaches the amplifier, so a command only ARMS the
clock and it starts on the first tick the speaker itself runs (5.55); and an
armed clock that is stopped before that moment keeps reporting "starting"
for kSoundWaitUs rather than an end the sound never had.

Split out of castle_emu.py (the mailbox and the routes) because it is the
one part of the emulator held to a C file line by line — tests/test_emu_clock
and tests/cxx/audio_clock_check.cpp check the two halves against each other.
"""

from __future__ import annotations

#: Rough playback clock: 96 kbps MP3 is ~12 kB of file per second.
BYTES_PER_S = 12000
#: The decoder and the I2S ring take this long to reach the amplifier, and
#: position_ms stays 0 for it. Lights aligned to a clock that started at the
#: command fire that much too early on the porch, so the emulator waits too.
SPEAKER_START_S = 0.5
#: sd_web_state.h's kSoundWaitUs: how long an ARMED clock keeps reporting
#: "starting" after the pipeline went quiet, so a slow decoder does not read
#: as a song that finished on the very next tick.
SOUND_WAIT_S = 1.5


def heard(started: float, ends: float, now: float) -> bool:
    """Has the speaker's own task run for this track yet?

    mirror_audio() clears g_clock_armed on the first tick the speaker is
    running, and the grace below belongs to an ARMED clock only: a track
    that was audible and then ended reports the end at once, however short
    it was. Here the speaker runs from SPEAKER_START_S into a track that
    lasts at least that long.
    """
    return ends > started + SPEAKER_START_S and now >= started + SPEAKER_START_S


def silence_until(
    started: float, ends: float, starting_until: float, now: float
) -> float:
    """When a stop lands, how long "starting" outlives the sound.

    mirror_audio(): a stop does not clear an ARMED clock — g_clock_armed is
    still up because the speaker was never heard from, so the board holds
    playing:true / position_ms:0 for the rest of kSoundWaitUs rather than
    reporting an end the sound never had. Stop a track that was audible and
    the end is immediate — 0.0 here.

    The arming belongs to the CLOCK, not to the track name (C3): a scene
    whose audio is not on the card is armed with nothing named, and used to
    lose its grace here because this asked whether a track was set.
    """
    if heard(started, ends, now) or now >= starting_until:
        return 0.0
    return starting_until


def audio_state(
    track: str, started: float, ends: float, starting_until: float, now: float
) -> tuple[bool, int]:
    """(playing, position_ms) — the two fields /api/status carries.

    A named track is sound only while it lasts: an authored scene goes on
    naming its track after the audio ends, and the board reports that
    silence honestly. Before the sound, an armed clock reports "starting"
    (playing:true at position 0) for the grace — including the case the
    sound never comes at all, which is what a command for audio the card
    does not have looks like on the device (C3/C4).
    """
    sounding = bool(track) and now < ends
    position = max(0, int((now - started - SPEAKER_START_S) * 1000)) if sounding else 0
    armed = not heard(started, ends, now)
    return sounding or (armed and now < starting_until), position
