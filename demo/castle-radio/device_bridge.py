"""Bounded bridge to the existing castle firmware; never pretends to pause/seek."""

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request

# The porch castle's private address; CASTLE_RADIO_HOST points the bridge at
# another castle (a bench unit, a QEMU build) without editing this file.
HOST = os.environ.get("CASTLE_RADIO_HOST", "10.27.27.81")
STATUS_PATH = "/api/status"
FILES_PATH = "/api/files"
# The castle's httpd has four sockets and answers on one task. Three browser
# pollers and an inventory sweep used to ask /api/status separately within the
# same second; one answer now serves everyone for a quarter second.
STATUS_CACHE_S = 0.25
# A queued command lands on the next 200 ms main-loop tick and the pipeline
# takes a moment more, so a poll fired right after "play" still names the old
# track. Until the castle agrees (or this long passes) the reply says what was
# asked for, so the page never flips back to the previous song.
SETTLE_S = 2.5
_CLOCK_LOCK = threading.Lock()
_clock = {
    "media": None,
    "scene": None,
    "track": None,
    "started": 0.0,
    "origin": "observed",
}
_STATUS_LOCK = threading.Lock()
_status_cache = {"at": 0.0, "state": None}
_expected = {"scene": None, "track": None, "until": 0.0}
_SHOW_LOCK = threading.Lock()
_show_control = {"stop": None}
_show_status = {"active": False, "track": None, "frames_sent": 0, "error": None}
_LIGHT_SPEC = re.compile(
    r"(?:(?:towerL|towerR|door):)?(?:[0-9a-fA-F]{6}|white|off|show|bars|chase|ends)(?:@(?:[1-9]|[1-9]\d|100))?"
)
_ZONE_LIGHT = {
    "left": ("towerL", "a832ff"),
    "door": ("door", "ff1f05"),
    "right": ("towerR", "4dff8c"),
}


def playback_clock(state, started_scene=None, started_track=None):
    """The castle's own clock when the firmware reports one (5.52), else an
    estimate counted from the moment this bridge sent the command."""
    if "position_ms" in state and started_scene is None and started_track is None:
        return _castle_clock(state)
    commanded = started_track if started_track is not None else started_scene
    return _estimated_clock(state, commanded, time.monotonic())


def _castle_clock(state):
    playing = bool(state.get("playing"))
    position = float(state.get("position_ms") or 0) / 1000
    return {
        "position_s": max(0.0, position) if playing else 0,
        "estimated": False,
        "origin": "castle",
        "playing": playing,
        "scene": state.get("scene", "stop"),
        "track": state.get("track", ""),
    }


def _playing_media(scene, track, commanded):
    """What is sounding: the commanded name when there is one, else what the
    castle reports; None for silence."""
    if commanded is not None:
        return commanded if commanded not in ("", "stop") else None
    return track or (scene if scene not in ("", "stop") else None)


def _estimated_clock(state, commanded, now):
    scene = state.get("scene", "stop")
    track = state.get("track", "")
    with _CLOCK_LOCK:
        media = _playing_media(scene, track, commanded)
        if commanded is not None or media != _clock["media"]:
            _clock.update(
                media=media,
                scene=scene,
                track=track,
                started=now,
                origin="command" if commanded is not None else "observed",
            )
        return {
            "position_s": max(0, now - _clock["started"]) if media else 0,
            "estimated": True,
            "origin": _clock["origin"],
            "playing": bool(media),
            "scene": scene,
            "track": track,
        }


def call(path, method="GET", data=None, timeout=8, fresh=False):
    if path == STATUS_PATH and method == "GET":
        with _STATUS_LOCK:
            cached = _status_cache["state"]
            if (
                not fresh
                and cached is not None
                and time.monotonic() - _status_cache["at"] < STATUS_CACHE_S
            ):
                return dict(cached)
    request = urllib.request.Request(f"http://{HOST}{path}", data=data, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
    if path == STATUS_PATH and method == "GET":
        with _STATUS_LOCK:
            _status_cache.update(at=time.monotonic(), state=dict(result))
        return dict(result)
    return result


def expect(scene=None, track=None):
    """Remember what a command asked for until the castle reports it."""
    with _STATUS_LOCK:
        _expected.update(scene=scene, track=track, until=time.monotonic() + SETTLE_S)
        _status_cache["state"] = None


def settle(state):
    """Overlay the pending command on a stale poll; drop it once it lands."""
    with _STATUS_LOCK:
        if _expected["until"] <= time.monotonic():
            return state
        scene, track = _expected["scene"], _expected["track"]
        scene_ok = scene is None or state.get("scene") == scene
        track_ok = track is None or state.get("track") == track
        if scene_ok and track_ok:
            _expected["until"] = 0.0
            return state
        state = dict(state)
        if scene is not None:
            state["scene"] = scene
        if track is not None:
            state["track"] = track
        state["settling"] = True
        if "position_ms" in state:
            state["playing"] = bool(track or (scene not in ("", "stop")))
            state["position_ms"] = 0
        return state


def status():
    state = settle(call(STATUS_PATH))
    with _SHOW_LOCK:
        show = dict(_show_status)
    version = state.get("version")
    return {
        "connected": True,
        "host": HOST,
        "state": state,
        "playback": playback_clock(state),
        "capabilities": {
            "scene": True,
            "files": True,
            "volume": True,
            "pir": True,
            "pause": False,
            "seek": False,
            "dynamic_lights": _version_at_least(version, (5, 51)),
            "position": "position_ms" in state,
            "track_end": _version_at_least(version, (5, 52)),
        },
        "light_show": show,
    }


def _version_at_least(value, wanted):
    match = re.match(r"^\s*(\d+)\.(\d+)", str(value))
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= wanted


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
    return call(STATUS_PATH, timeout=3, fresh=True)


def _await_track(filename, stop_event, first):
    """The status naming `filename`, or None once the castle has moved on.
    The first call tolerates the mailbox: a play lands on the next 200 ms
    tick, so the status right after the command still names the previous
    track."""
    state = call(STATUS_PATH, timeout=3, fresh=True)
    for _ in range(15 if first else 0):
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
    started = _align(filename, time.monotonic() + 0.24, stop_event, first=True)
    if started is None:
        return
    for index, (at, spec) in enumerate(frames):
        if stop_event.wait(max(0, started + at - time.monotonic())):
            return
        call("/api/light?" + urllib.parse.urlencode({"c": spec}), "POST", timeout=3)
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
    if current and not stop_event.is_set():
        try:
            call("/api/light?c=off", "POST", timeout=3)
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


# Every value that reaches a castle URL is the castle's own spelling (a name
# it listed, a scene it reported) or is rebuilt from known tokens and ints.
# Nothing the browser sent is forwarded as-is.
_ZONES = {"towerL": "towerL", "towerR": "towerR", "door": "door"}
_LIGHT_WORDS = {
    word: word for word in ("white", "off", "show", "bars", "chase", "ends")
}
_TONES = {
    f"test_{n}.mp3": f"test_{n}.mp3" for n in ("sweep", "1k", "200", "4k", "silence")
}
_PLAIN = {a: "/api/" + a for a in ("stop", "blackout", "show/start", "show/stop")}
_COOLDOWNS = {30: 30, 60: 60, 120: 120}


def _installed_scene(name):
    for scene in call(STATUS_PATH).get("scenes", "").split(","):
        if scene == name:
            return scene
    raise ValueError("This light show is not installed in the current firmware.")


def _castle_file(name, missing):
    for row in call(FILES_PATH):
        if row.get("name") == name and not row.get("dir"):
            return str(row["name"])
    raise ValueError(missing)


def _light_spec(value):
    if not _LIGHT_SPEC.fullmatch(value):
        raise ValueError("Choose a valid castle light test.")
    zone, _, spec = value.rpartition(":")
    spec, _, pct = spec.partition("@")
    colour = _LIGHT_WORDS.get(spec) or f"{int(spec, 16):06x}"
    out = f"{_ZONES[zone]}:{colour}" if zone else colour
    return f"{out}@{int(pct)}" if pct else out


def _percent(value, what="Volume"):
    number = int(value)
    if not 0 <= number <= 100:
        raise ValueError(f"{what} must be between 0 and 100.")
    return number


def _scene_path(body):
    scene = _installed_scene(str(body.get("scene", "")))
    return "/api/scene?" + urllib.parse.urlencode({"s": scene}), scene, None


def _file_path(body):
    filename = str(body.get("file", ""))
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or filename.rsplit(".", 1)[-1].lower() not in ("mp3", "opus", "wav")
    ):
        raise ValueError("Choose a playable castle audio file.")
    filename = _castle_file(filename, "That audio file is not on the castle.")
    return "/api/play?" + urllib.parse.urlencode({"f": filename}), None, filename


def _light_path(body):
    spec = _light_spec(str(body.get("value", "")))
    stop_imported_show()
    return "/api/light?" + urllib.parse.urlencode({"c": spec}), None, None


def _tone_path(body):
    wanted = _TONES.get(str(body.get("file", "")))
    if wanted is None:
        raise ValueError("Choose a diagnostic tone.")
    volume = _percent(body.get("volume", 50))
    filename = _castle_file(wanted, "That diagnostic tone is not on the castle.")
    stop_imported_show()
    call("/api/volume?" + urllib.parse.urlencode({"v": volume}), "POST")
    time.sleep(0.3)
    return "/api/play?" + urllib.parse.urlencode({"f": filename}), None, None


def _volume_path(body):
    volume = _percent(body.get("volume", 0))
    return "/api/volume?" + urllib.parse.urlencode({"v": volume}), None, None


def _pir_path(body):
    cooldown = _COOLDOWNS.get(int(body.get("cooldown", 60)))
    if cooldown is None:
        raise ValueError("Choose a supported motion cooldown.")
    query = {"armed": int(bool(body.get("armed"))), "cooldown": cooldown}
    return "/api/pir?" + urllib.parse.urlencode(query), None, None


_BUILDERS = {
    "scene": _scene_path,
    "file": _file_path,
    "light": _light_path,
    "tone": _tone_path,
    "volume": _volume_path,
    "pir": _pir_path,
}


def command(body, imported_show=None):
    action = str(body.get("action", ""))
    scene = filename = None
    if action in _PLAIN:
        path = _PLAIN[action]
    elif action in _BUILDERS:
        path, scene, filename = _BUILDERS[action](body)
    else:
        raise ValueError("This control is not supported by the running firmware.")
    if action in _PLAIN or action == "scene":
        stop_imported_show()
    if action == "file" and imported_show:
        if not _version_at_least(call(STATUS_PATH).get("version"), (5, 51)):
            raise ValueError(
                "Generated imported lights require castle firmware 5.51 or newer."
            )
        # Start the continuous firmware texture first. The generated cue runner
        # replaces it one bounded frame at a time once raw playback begins.
        call("/api/light?c=show", "POST")
        time.sleep(0.3)
    result = call(path, "POST")
    if action == "scene":
        expect(scene=scene)
        playback_clock({"scene": scene}, started_scene=scene)
    elif action == "file":
        expect(scene="stop", track=filename)
        playback_clock({"scene": "stop", "track": filename}, started_track=filename)
        if imported_show:
            start_imported_show(
                filename, imported_show.get("cues"), imported_show.get("duration")
            )
    elif action in ("stop", "blackout", "show/stop"):
        expect(scene="stop", track="")
        playback_clock({"scene": "stop"}, started_scene="stop")
    elif action in ("volume", "pir"):
        with _STATUS_LOCK:
            _status_cache["state"] = None
    return result
