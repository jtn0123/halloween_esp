"""SD inventory and verified, additive audio transfers for the radio library."""

import concurrent.futures
import http.client
import json
import threading
import urllib.parse
import zlib
from pathlib import Path

import device_bridge

_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_LOCK = threading.Lock()
_JOBS = {}
AUDIO_SUFFIXES = ("mp3", "opus", "wav")


def playback_path(library, row):
    named = row.get("playback_file")
    if named:
        path = library / Path(named).name
        if path.is_file():
            return path
    return next(
        (
            library / f"{row['key']}.{suffix}"
            for suffix in AUDIO_SUFFIXES
            if (library / f"{row['key']}.{suffix}").is_file()
        ),
        None,
    )


def job(key):
    with _LOCK:
        value = _JOBS.get(key)
        if value is None:
            raise ValueError("Unknown sync job")
        return dict(value)


def inventory(root, library, rows):
    state = device_bridge.call("/api/status")
    files = device_bridge.call("/api/files")
    scenes = device_bridge.call("/api/files?d=scenes")
    installed = set(state.get("scenes", "").split(","))
    audio = {f["name"]: f["size"] for f in files if not f.get("dir")}
    scene_audio = {f["name"] for f in scenes if not f.get("dir")}
    result = {}
    for path in (root / "media").glob("*.mp3"):
        scene = path.stem.split("_", 1)[1]
        ready = scene in installed and path.name in scene_audio
        result[path.name] = {
            "status": "ready" if ready else "missing",
            "audio": path.name in scene_audio,
            "lights": scene in installed,
            "can_sync": scene in installed,
        }
    for row in rows:
        key = row["key"]
        path = playback_path(library, row)
        present = path is not None and audio.get(path.name) == path.stat().st_size
        result[key] = {
            "status": "audio_only" if present else "missing",
            "audio": present,
            "lights": key in installed,
            "can_sync": True,
            "filename": path.name if present else None,
            "bytes": audio.get(path.name) if present else None,
        }
    with _LOCK:
        jobs = {k: dict(v) for k, v in _JOBS.items()}
    known_files = {path.name for row in rows if (path := playback_path(library, row))}
    return {
        "tracks": result,
        "jobs": jobs,
        "other_audio": sorted(
            (
                {"name": name, "bytes": size}
                for name, size in audio.items()
                if Path(name).suffix.removeprefix(".") in AUDIO_SUFFIXES
                and name not in known_files
            ),
            key=lambda row: row["name"],
        ),
    }


def delete_audio(name):
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or Path(name).suffix.removeprefix(".").lower() not in AUDIO_SUFFIXES
    ):
        raise ValueError("Choose a castle audio file.")
    # The castle's own spelling of the name, from its listing, is what goes
    # back into the URL; the browser's spelling only selects it.
    listed = next(
        (
            str(row["name"])
            for row in device_bridge.call("/api/files")
            if row.get("name") == name and not row.get("dir")
        ),
        None,
    )
    if listed is None:
        raise ValueError("That audio file is not on the castle.")
    return device_bridge.call(
        "/api/files/" + urllib.parse.quote(listed, safe=""), "DELETE"
    )


def start(root, library, rows, key):
    if not isinstance(key, str) or Path(key).name != key:
        raise ValueError("Unknown song")
    row = next((row for row in rows if row["key"] == key), None)
    if row is not None:
        key = str(row["key"])
        source, route = playback_path(library, row), "/api/files"
    else:
        # A built-in scene track: the file the media directory actually holds.
        source = next(
            (p for p in (root / "media").glob("*.mp3") if p.name == key), None
        )
        if source is None:
            raise ValueError("Song audio is no longer available on this computer.")
        key, route = source.name, "/api/scenes"
        scene = source.stem.split("_", 1)[-1]
        if source.suffix != ".mp3" or scene not in device_bridge.call(
            "/api/status"
        ).get("scenes", "").split(","):
            raise ValueError(
                "The light show needs matching firmware before this scene can sync."
            )
    if source is None or not source.is_file():
        raise ValueError("Song audio is no longer available on this computer.")
    # Snapshot before queueing: local deletion must not change an in-flight transfer.
    data = source.read_bytes()
    with _LOCK:
        if key in _JOBS and not _JOBS[key]["done"]:
            return dict(_JOBS[key])
        job = {
            "key": key,
            "done": False,
            "error": None,
            "phase": "Queued",
            "bytes": len(data),
            "sent_bytes": 0,
            "percent": 0,
        }
        _JOBS[key] = job
    _POOL.submit(transfer, key, route, source.name, data)
    return dict(job)


def transfer(key, route, name, data):
    try:
        upload_with_progress(key, route, name, data)
        with _LOCK:
            _JOBS[key].update(
                done=True,
                phase="Audio verified on castle",
                sent_bytes=len(data),
                percent=100,
            )
    except (OSError, ValueError, http.client.HTTPException) as exc:
        with _LOCK:
            _JOBS[key].update(done=True, phase="Sync failed", error=str(exc))


def upload_with_progress(key, route, name, data, connection_factory=None):
    """Stream chunks so the UI sees bytes handed to the castle socket."""
    factory = connection_factory or (
        lambda: http.client.HTTPConnection(device_bridge.HOST, timeout=600)
    )
    connection = factory()
    path = f"{route}/{urllib.parse.quote(name)}"
    try:
        connection.putrequest("PUT", path)
        connection.putheader("Content-Length", str(len(data)))
        connection.putheader("Content-Type", "application/octet-stream")
        connection.endheaders()
        sent = 0
        for offset in range(0, len(data), 32 * 1024):
            block = data[offset : offset + 32 * 1024]
            connection.send(block)
            sent += len(block)
            with _LOCK:
                _JOBS[key].update(
                    phase="Uploading audio to castle",
                    sent_bytes=sent,
                    percent=min(99, round(sent * 100 / len(data), 1)),
                )
        with _LOCK:
            _JOBS[key].update(phase="Verifying castle SD copy", percent=99)
        response = connection.getresponse()
        raw = response.read()
        if response.status >= 400:
            raise OSError(
                f"Castle upload failed ({response.status}): {raw.decode(errors='replace')}"
            )
        result = json.loads(raw)
        if result.get("bytes") != len(data):
            raise OSError(
                f"Castle verified {result.get('bytes', 0)} of {len(data)} bytes"
            )
        reported_crc = result.get("crc32")
        if reported_crc is not None and int(str(reported_crc), 16) != zlib.crc32(data):
            raise OSError("Castle SD verification failed: CRC mismatch")
    finally:
        connection.close()
