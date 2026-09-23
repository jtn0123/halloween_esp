"""Local demo adapter around the project's importer and Demucs splitter.

Imports stay isolated; explicit device actions connect to the porch castle.
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import job_progress

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / ".radio-data"
DATA.mkdir(exist_ok=True)
LIBRARY = DATA / "tracks"
LIBRARY.mkdir(exist_ok=True)
os.environ.update(
    CASTLE_TRACKS=str(LIBRARY),
    CASTLE_HOST="",
    CASTLE_SCENES=str(DATA / "scenes.yaml"),
    CASTLE_BUILD=str(DATA / "build"),
)
sys.path.insert(0, str(ROOT / "tools"))
from import_scene import fit_to_density, scene_block  # noqa: E402
from import_track import crate_analysis  # noqa: E402

#: Preparation jobs by id, as the page reads them: a record of mixed
#: strings, flags and progress numbers, which is what /radio/jobs serves.
JOBS: dict[str, dict[str, object]] = {}
LOCK = threading.RLock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
#: Per unfinished job, outside the record the page reads: the pool's future
#: (a job still waiting is cancelled there) and the Event that stops a child.
HANDLES: dict[str, tuple[concurrent.futures.Future, threading.Event]] = {}
CATALOG = DATA / "catalog.json"
FILE_PREFIX = "file:"
QUALITY_BITRATES = {
    "standard": {"mp3": 96, "opus": 64},
    "high": {"mp3": 160, "opus": 96},
    "max": {"mp3": 192, "opus": 128},
}


def track_manifest():
    path = LIBRARY / "tracks.json"
    return json.loads(path.read_text()) if path.exists() else {}


def source_metadata(key, manifest=None):
    entry = (manifest or track_manifest()).get(key, {})
    source = str(entry.get("source") or "")
    audio = entry.get("audio") or {}
    quality = next(
        (
            name
            for name, rates in QUALITY_BITRATES.items()
            if rates.get(str(audio.get("format"))) == audio.get("bitrate")
        ),
        "standard",
    )
    if source.startswith(("http://", "https://")):
        parsed = urlsplit(source)
        label = parsed.netloc + parsed.path
        return {
            "source_kind": "link",
            "source_label": label.rstrip("/"),
            "source_url": source,
            "source_available": True,
            "playback_format": audio.get("format"),
            "playback_bitrate": audio.get("bitrate"),
            "playback_bytes": audio.get("bytes"),
            "playback_quality": quality,
        }
    if source.startswith(FILE_PREFIX):
        path = Path(source.removeprefix(FILE_PREFIX))
        return {
            "source_kind": "file",
            "source_label": path.name,
            "source_url": None,
            "source_available": path.is_file(),
            "playback_format": audio.get("format"),
            "playback_bitrate": audio.get("bitrate"),
            "playback_bytes": audio.get("bytes"),
            "playback_quality": quality,
        }
    return {
        "source_kind": "unknown",
        "source_label": "Original source unavailable",
        "source_url": None,
        "source_available": False,
        "playback_format": audio.get("format"),
        "playback_bitrate": audio.get("bitrate"),
        "playback_bytes": audio.get("bytes"),
        "playback_quality": quality,
    }


def catalog():
    rows = json.loads(CATALOG.read_text()) if CATALOG.exists() else []
    manifest = track_manifest()
    return [{**source_metadata(row["key"], manifest), **row} for row in rows]


def update(job, **values):
    with LOCK:
        if values.get("done"):
            values["finished_at"] = time.time()
        job.update(values)


def report(job, found_title=None, **values):
    """A progress record from a tool. The name the downloader found fills an
    empty title and never replaces one somebody typed."""
    if found_title and not job.get("title"):
        values["title"] = found_title
    if values:
        update(job, **values)


def run_tool(job, script, args, timeout, extra_env=None):
    handle = HANDLES.get(job["id"])
    return job_progress.run(
        [sys.executable, "-u", str(ROOT / "tools" / script), *args],
        timeout,
        "split" if script == "stems.py" else "import",
        lambda **values: report(job, **values),
        extra_env,
        handle[1] if handle else None,
    )


def submit(job):
    stop = threading.Event()
    with LOCK:
        job["queued_at"] = time.time()
        future = POOL.submit(
            prepare,
            job,
            job["source"],
            job["title"],
            job["split"],
            job.get("audio_format", "mp3"),
            job.get("audio_quality", "standard"),
        )
        HANDLES[job["id"]] = (future, stop)


def cancel(tid):
    """Stop a job that is waiting or running. A waiting one never starts; a
    running one has its tools killed and ends as Cancelled, not as a failure."""
    with LOCK:
        job, handle = JOBS.get(tid), HANDLES.get(tid)
        if not job or job["done"] or not handle:
            raise ValueError("That job is not waiting or running.")
        job["cancelled"] = True
        handle[1].set()
        if handle[0].cancel():
            finish_cancelled(job)
    return job


def finish_cancelled(job):
    HANDLES.pop(job["id"], None)
    update(
        job,
        phase="Cancelled",
        done=True,
        error=None,
        percent=None,
        detail="Cancelled before it finished. Retry to prepare it again.",
    )


def rename(key, title):
    title = " ".join(str(title or "").split())[:200]
    if not title:
        raise ValueError("Type a name for this song.")
    with LOCK:
        rows = json.loads(CATALOG.read_text()) if CATALOG.exists() else []
        row = next((r for r in rows if r["key"] == key), None)
        if row is None:
            raise ValueError("This song is no longer in the library")
        row["title"] = title
        temp = CATALOG.with_suffix(".tmp")
        temp.write_text(json.dumps(rows))
        temp.replace(CATALOG)
    return {"key": key, "title": title}


def zone_cues(marks, zone):
    """Reuse density tuning and the actual detected timing/velocity data."""
    # One cue is [at, zone, intensity, decay] — the browser renderer's shape.
    result: list[list[float | str]] = []
    for hits in marks.values():
        decay, scale = fit_to_density(hits, 0.92)
        result.extend(
            [round(hit[0], 3), zone, min(1, hit[1] * scale), decay] for hit in hits
        )
    return result


PLAYBACK_FORMATS = {
    "mp3": (96, 44100),
    "opus": (64, 48000),
    "wav": (96, 44100),
}


def playback_format(value):
    value = str(value or "mp3").lower()
    if value not in PLAYBACK_FORMATS:
        raise ValueError("Choose MP3, Ogg Opus, or PCM WAV.")
    return value


def playback_quality(value):
    value = str(value or "standard").lower()
    if value not in QUALITY_BITRATES:
        raise ValueError("Choose Standard, High, or Maximum audio quality.")
    return value


def playback_options(audio_format, quality):
    audio_format = playback_format(audio_format)
    quality = playback_quality(quality)
    if audio_format == "wav":
        return 0, 44100
    return QUALITY_BITRATES[quality][audio_format], PLAYBACK_FORMATS[audio_format][1]


def _cookies(header):
    pairs = (part.strip().partition("=") for part in (header or "").split(";"))
    return {name: value for name, sep, value in pairs if sep}


def cookie_audio_format(header):
    return _cookies(header).get("castle_audio_format")


def cookie_audio_quality(header):
    return _cookies(header).get("castle_audio_quality")


def reprocess_job(key, audio_format, audio_quality, split=None):
    row = next((item for item in catalog() if item["key"] == key), None)
    entry = track_manifest().get(key, {})
    source = str(entry.get("source") or "")
    if row is None or not source:
        raise ValueError("That song has no saved source to reprocess.")
    if source.startswith(FILE_PREFIX):
        source = source.removeprefix(FILE_PREFIX)
        if not Path(source).is_file():
            raise ValueError("The saved source file is no longer available.")
    else:
        parsed = urlsplit(source)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("That song has no usable saved source.")
    return {
        "id": str(row["key"]),
        "source": source,
        "title": row["title"],
        "split": row.get("split", True) if split is None else split is True,
        "audio_format": playback_format(audio_format),
        "audio_quality": playback_quality(audio_quality),
        "source_name": (
            row.get("source_label") if row.get("source_kind") == "file" else None
        ),
        "phase": "Queued for reprocessing",
        "done": False,
        "error": None,
    }


def prepare(job, source, title, split, audio_format, audio_quality="standard"):
    tid = job["id"]
    try:
        if job.get("cancelled"):
            raise job_progress.Cancelled("Cancelled")
        update(job, phase="Importing and analyzing", error=None)
        bitrate, sample_rate = playback_options(audio_format, audio_quality)
        # The source (a link someone pasted, or the upload's path) travels in
        # the environment, never as an argument: see import_track.py.
        args = [
            "--id",
            tid,
            "--channels",
            "2",
            "--format",
            audio_format,
            "--bitrate",
            str(bitrate),
            "--sample-rate",
            str(sample_rate),
        ]
        if not source.startswith(("http://", "https://")):
            args += ["--keep-source"]
        run_tool(job, "import_track.py", args, 1000, {"CASTLE_IMPORT_SOURCE": source})
        path = LIBRARY / f"{tid}.{audio_format}"
        update(
            job,
            phase="Generating light show",
            percent=None,
            detail="Analyzing the full song",
        )
        samples, marks = crate_analysis(path, 1.1, True)
        duration = samples / 44100
        cues = []
        for band, hits in marks.items():
            zone = {
                "onset_low": "door",
                "onset_mid": "left",
                "onset_high": "right",
            }.get(band, "door")
            cues += zone_cues({band: hits}, zone)
        split_error = None
        has_split = False
        if split:
            update(job, phase="Separating voice and background")
            try:
                run_tool(job, "stems.py", [tid], 900)
                analysis = json.loads(
                    (LIBRARY / "stems" / tid / "analysis.json").read_text()
                )
                layers = analysis["layers"]
                cues = zone_cues(layers["vocals"]["both"]["onsets"], "door")
                cues += zone_cues(layers["backing"]["left"]["onsets"], "left")
                cues += zone_cues(layers["backing"]["right"]["onsets"], "right")
                has_split = True
            except job_progress.Cancelled:
                raise  # a ValueError too, but not a split that failed
            except (ValueError, subprocess.TimeoutExpired) as exc:
                split_error = str(exc)
        update(
            job,
            phase="Saving prepared show",
            percent=None,
            detail="Saving audio and generated cues",
        )
        manifest = json.loads((LIBRARY / "tracks.json").read_text())[tid]
        details = source_metadata(tid, {tid: manifest})
        if job.get("source_name"):
            details["source_label"] = job["source_name"]
        record = dict(
            key=tid,
            title=title or manifest.get("title") or "Imported song",
            duration=duration,
            url=f"/radio/audio/{path.name}",
            playback_file=path.name,
            split=has_split,
            split_error=split_error,
            cues=sorted(cues),
            artist="Your imports",
            style="Voice + background" if has_split else "Auto rhythm",
            **details,
        )
        # Keep the existing generated scene recipe alongside the demo's split-aware preview cues.
        (DATA / f"{tid}.yaml").write_text(scene_block(tid, duration, marks))
        with LOCK:
            rows = [r for r in catalog() if r["key"] != tid] + [record]
            temp = CATALOG.with_suffix(".tmp")
            temp.write_text(json.dumps(rows))
            temp.replace(CATALOG)
        update(
            job,
            phase="Ready in demo"
            if not split_error
            else "Ready · split needs attention",
            done=True,
            percent=100,
            detail="Audio and lights are ready in this demo",
            title=record["title"],
            result=record,
        )
    except job_progress.Cancelled:
        finish_cancelled(job)
    except Exception as exc:
        update(job, phase="Import failed", done=True, error=str(exc))
    finally:
        HANDLES.pop(tid, None)
