"""Bounded bridge to the existing castle firmware; never pretends to pause/seek."""

import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

HOST = "10.27.27.81"
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
    r"(?:(?:towerL|towerR|door):)?(?:[0-9a-fA-F]{6}|white|off|show|bars|chase|ends)(?:@(?:[1-9]|[1-9][0-9]|100))?"
)
_ZONE_LIGHT = {
    "left": ("towerL", "a832ff"),
    "door": ("door", "ff1f05"),
    "right": ("towerR", "4dff8c"),
}


def playback_clock(state, started_scene=None, started_track=None):
    """The castle's own clock when the firmware reports one (5.52), else an
    estimate counted from the moment this bridge sent the command."""
    now = time.monotonic()
    if "position_ms" in state and started_scene is None and started_track is None:
        scene = state.get("scene", "stop")
        track = state.get("track", "")
        playing = bool(state.get("playing"))
        return {
            "position_s": max(0.0, float(state.get("position_ms") or 0) / 1000)
            if playing
            else 0,
            "estimated": False,
            "origin": "castle",
            "playing": playing,
            "scene": scene,
            "track": track,
        }
    with _CLOCK_LOCK:
        scene = state.get("scene", "stop")
        track = state.get("track", "")
        media = track or (scene if scene not in ("", "stop") else None)
        commanded = started_track if started_track is not None else started_scene
        if commanded is not None:
            media = commanded if commanded not in ("", "stop") else None
        if commanded is not None or media != _clock["media"]:
            _clock["media"] = media
            _clock["scene"] = scene
            _clock["track"] = track
            _clock["started"] = now
            _clock["origin"] = "command" if commanded is not None else "observed"
        return {
            "position_s": max(0, now - _clock["started"]) if media else 0,
            "estimated": True,
            "origin": _clock["origin"],
            "playing": bool(media),
            "scene": scene,
            "track": track,
        }


def call(path, method="GET", data=None, timeout=8, fresh=False):
    if path == "/api/status" and method == "GET":
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
    if path == "/api/status" and method == "GET":
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
    state = settle(call("/api/status"))
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
    try:
        return tuple(int(part) for part in str(value).split(".")[:2]) >= wanted
    except ValueError:
        return False


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


def _align(filename, started, stop_event, first=False):
    """Pull the frame clock onto the castle's own (5.52 position_ms).

    Returns the corrected start, or None once the castle has moved on. The
    first call tolerates the mailbox: a play lands on the next 200 ms tick,
    so the status right after the command still names the previous track."""
    state = call("/api/status", timeout=3, fresh=True)
    for _ in range(15):
        if state.get("track") == filename or not first:
            break
        if stop_event.wait(0.2):
            return None
        state = call("/api/status", timeout=3, fresh=True)
    if state.get("track") != filename:
        return None
    if "position_ms" not in state:
        return started
    if not state.get("playing"):
        # Queued, not decoding yet: hold the first frame for the real start.
        for _ in range(10):
            if stop_event.wait(0.2):
                return None
            state = call("/api/status", timeout=3, fresh=True)
            if state.get("track") != filename:
                return None
            if state.get("playing"):
                break
        else:
            return started
    return time.monotonic() - float(state.get("position_ms") or 0) / 1000


def _run_imported_show(filename, frames, duration, stop_event):
    started = time.monotonic() + 0.24
    sent = 0
    error = None
    try:
        aligned = _align(filename, started, stop_event, first=True)
        if aligned is None:
            return
        started = aligned
        for index, (at, spec) in enumerate(frames):
            delay = started + at - time.monotonic()
            if stop_event.wait(max(0, delay)):
                return
            call("/api/light?" + urllib.parse.urlencode({"c": spec}), "POST", timeout=3)
            sent += 1
            with _SHOW_LOCK:
                if stop_event is _show_control["stop"]:
                    _show_status["frames_sent"] = sent
            if index and index % 20 == 0:
                aligned = _align(filename, started, stop_event)
                if aligned is None:
                    return
                started = aligned
        stop_event.wait(max(0, started + float(duration or 0) - time.monotonic()))
    except OSError as exc:
        error = str(exc)
    finally:
        with _SHOW_LOCK:
            current = stop_event is _show_control["stop"]
            if current:
                _show_status.update(
                    active=False, track=None, frames_sent=sent, error=error
                )
        if current and not stop_event.is_set():
            try:
                call("/api/light?c=off", "POST", timeout=3)
            except OSError:
                pass


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


def command(body, imported_show=None):
    action = body.get("action")
    if action == "scene":
        scene = str(body.get("scene", ""))
        if scene not in call("/api/status").get("scenes", "").split(","):
            raise ValueError(
                "This light show is not installed in the current firmware."
            )
        path = "/api/scene?" + urllib.parse.urlencode({"s": scene})
    elif action == "file":
        filename = str(body.get("file", ""))
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or filename.rsplit(".", 1)[-1].lower() not in ("mp3", "opus", "wav")
        ):
            raise ValueError("Choose a playable castle audio file.")
        files = call("/api/files")
        if not any(row.get("name") == filename and not row.get("dir") for row in files):
            raise ValueError("That audio file is not on the castle.")
        path = "/api/play?" + urllib.parse.urlencode({"f": filename})
    elif action == "light":
        value = str(body.get("value", ""))
        if not _LIGHT_SPEC.fullmatch(value):
            raise ValueError("Choose a valid castle light test.")
        stop_imported_show()
        path = "/api/light?" + urllib.parse.urlencode({"c": value})
    elif action == "tone":
        filename = str(body.get("file", ""))
        volume = int(body.get("volume", 50))
        if not re.fullmatch(r"test_(?:sweep|1k|200|4k|silence)\.mp3", filename):
            raise ValueError("Choose a diagnostic tone.")
        if not 0 <= volume <= 100:
            raise ValueError("Volume must be between 0 and 100.")
        files = call("/api/files")
        if not any(row.get("name") == filename and not row.get("dir") for row in files):
            raise ValueError("That diagnostic tone is not on the castle.")
        stop_imported_show()
        call("/api/volume?" + urllib.parse.urlencode({"v": volume}), "POST")
        time.sleep(0.3)
        path = "/api/play?" + urllib.parse.urlencode({"f": filename})
    elif action == "volume":
        volume = int(body.get("volume", 0))
        if not 0 <= volume <= 100:
            raise ValueError("Volume must be between 0 and 100.")
        path = "/api/volume?" + urllib.parse.urlencode({"v": volume})
    elif action == "pir":
        cooldown = int(body.get("cooldown", 60))
        if cooldown not in (30, 60, 120):
            raise ValueError("Choose a supported motion cooldown.")
        path = "/api/pir?" + urllib.parse.urlencode(
            {"armed": int(bool(body.get("armed"))), "cooldown": cooldown}
        )
    elif action in ("stop", "blackout", "show/start", "show/stop"):
        path = "/api/" + action
    else:
        raise ValueError("This control is not supported by the running firmware.")
    if action in ("stop", "blackout", "show/start", "show/stop", "scene"):
        stop_imported_show()
    if action == "file" and imported_show:
        if not _version_at_least(call("/api/status").get("version"), (5, 51)):
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
