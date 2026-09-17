"""The generated light show for an imported track, run on the castle's clock.

device_bridge owns the link to the castle (`call`, STATUS_PATH, the speaker's
start delay) and re-exports every public name here, so server.py and
device_site.py keep their one import. This module reaches back through the
module object rather than binding `call` by value: a test that patches
`device_bridge.call` must still intercept the frames this runner sends.
"""

import math
import threading
import time
import urllib.parse

# A mid-song realign gets this many polls to see the track it is aligning to.
# One stale mirror answer used to end the light show for the rest of the song.
REALIGN_POLLS = 3
_SHOW_LOCK = threading.Lock()
_show_control = {"stop": None}
_show_status = {"active": False, "track": None, "frames_sent": 0, "error": None}
_ZONE_LIGHT = {
    "left": ("towerL", "a832ff"),
    "door": ("door", "ff1f05"),
    "right": ("towerR", "4dff8c"),
}


def _bridge():
    """The bridge module itself, looked up at call time (see the docstring)."""
    import device_bridge

    return device_bridge


def _call(path, *args, **kwargs):
    return _bridge().call(path, *args, **kwargs)


def _status(**kwargs):
    bridge = _bridge()
    return bridge.call(bridge.STATUS_PATH, **kwargs)


def imported_light_frames(cues, frame_s=0.25):
    """Reduce dense analysis hits to one firmware-safe update per mailbox tick."""
    strongest = {}
    for cue in cues or ():
        if (
            not isinstance(cue, (list, tuple))
            or len(cue) < 3
            or cue[1] not in _ZONE_LIGHT
        ):
            continue
        try:
            at, intensity = max(0.0, float(cue[0])), max(0.0, min(1.0, float(cue[2])))
        except (TypeError, ValueError):
            continue
        bucket = math.floor(at / frame_s) * frame_s
        if bucket not in strongest or intensity > strongest[bucket][1]:
            strongest[bucket] = (cue[1], intensity)
    result = []
    for at, (zone_name, intensity) in sorted(strongest.items()):
        zone, color = _ZONE_LIGHT[zone_name]
        result.append(
            (round(at, 3), f"{zone}:{color}@{max(5, round(intensity * 100))}")
        )
    return result


def show_status():
    """A snapshot of the running show, as /radio/device reports it."""
    with _SHOW_LOCK:
        return dict(_show_status)


def stop_imported_show():
    with _SHOW_LOCK:
        if _show_control["stop"]:
            _show_control["stop"].set()
        _show_control["stop"] = None
        _show_status.update(active=False, track=None)


def _fresh_status(stop_event):
    """The castle's status after one mailbox tick, or None once stopped."""
    if stop_event.wait(0.2):
        return None
    return _status(timeout=3, fresh=True)


def _await_track(filename, stop_event, first):
    """The status naming `filename`, or None once the castle has moved on.
    The first call tolerates the mailbox: a play lands on the next 200 ms
    tick, so the status right after the command still names the previous
    track; a mid-song realign gets REALIGN_POLLS for the same reason."""
    state = _status(timeout=3, fresh=True)
    for _ in range(15 if first else REALIGN_POLLS):
        if state.get("track") == filename:
            break
        state = _fresh_status(stop_event)
        if state is None:
            return None
    return state if state.get("track") == filename else None


def _sounding(state):
    """Firmware 5.55 holds position_ms at 0 until the speaker itself runs;
    a moving clock is the first sound, not the pipeline's start."""
    return bool(state.get("playing")) and int(state.get("position_ms") or 0) > 0


def _await_playing(filename, state, stop_event):
    """Poll until the castle is sounding `filename` (about two seconds at
    most); None once it stopped or moved on. The status returned may still
    say not playing when the window ran out."""
    for _ in range(10):
        if _sounding(state):
            break
        state = _fresh_status(stop_event)
        if state is None or state.get("track") != filename:
            return None
    return state


def _align(filename, started, stop_event, first=False):
    """Pull the frame clock onto the castle's own (5.52 position_ms).

    Returns the corrected start, or None once the castle has moved on."""
    state = _await_track(filename, stop_event, first)
    if state is None:
        return None
    if "position_ms" not in state:
        return started
    if not _sounding(state):
        # Queued or buffering, not sounding yet: hold the first frame.
        state = _await_playing(filename, state, stop_event)
        if state is None:
            return None
        if not _sounding(state):
            return started
    return time.monotonic() - float(state.get("position_ms") or 0) / 1000


def _imported_frames(filename, frames, duration, stop_event):
    """Send each frame on the castle's clock, yielding the running count."""
    seed = time.monotonic() + 0.24 + _bridge().SPEAKER_START_S
    started = _align(filename, seed, stop_event, first=True)
    if started is None:
        return
    # Firmware 5.63 runs the song's own show from the card when there is a
    # cue file, and /api/status says how big it is. Frames from here would
    # only paint four solid colours a second over the real thing.
    if int(_status(timeout=3).get("cues") or 0) > 0:
        _show_status["castle_cues"] = True
        return
    for index, (at, spec) in enumerate(frames):
        if stop_event.wait(max(0, started + at - time.monotonic())):
            return
        _call("/api/light?" + urllib.parse.urlencode({"c": spec}), "POST", timeout=3)
        yield index + 1
        if index and index % 20 == 0:
            started = _align(filename, started, stop_event)
            if started is None:
                return
    stop_event.wait(max(0, started + float(duration or 0) - time.monotonic()))


def _note_frames_sent(stop_event, sent):
    with _SHOW_LOCK:
        if stop_event is _show_control["stop"]:
            _show_status["frames_sent"] = sent


def _finish_imported_show(stop_event, sent, error):
    with _SHOW_LOCK:
        current = stop_event is _show_control["stop"]
        if current:
            _show_status.update(active=False, track=None, frames_sent=sent, error=error)
    if current and _show_status.pop("castle_cues", False):
        return  # "off" is a colour too: it would black out the castle's own show
    if current:  # stopped too: a frame in flight would paint after /api/stop
        try:
            _call("/api/light?c=off", "POST", timeout=3)
        except OSError:
            pass


def _run_imported_show(filename, frames, duration, stop_event):
    sent = 0
    error = None
    try:
        for sent in _imported_frames(filename, frames, duration, stop_event):
            _note_frames_sent(stop_event, sent)
    except OSError as exc:
        error = str(exc)
    finally:
        _finish_imported_show(stop_event, sent, error)


def start_imported_show(filename, cues, duration):
    stop_imported_show()
    frames = imported_light_frames(cues)
    stop_event = threading.Event()
    with _SHOW_LOCK:
        _show_control["stop"] = stop_event
        _show_status.update(
            active=True,
            track=filename,
            frames_sent=0,
            error=None,
            frames_total=len(frames),
        )
    thread = threading.Thread(
        target=_run_imported_show,
        args=(filename, frames, duration, stop_event),
        name="castle-imported-lights",
        daemon=True,
    )
    thread.start()
