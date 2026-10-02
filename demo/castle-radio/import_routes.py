"""Turning an import request into a queued preparation job.

`/radio/import` is the one route whose body is the user's own file, so it is
the one place the server reads a request before it knows what the request is:
a JSON link (a URL to fetch) and a raw upload (up to 100 MB of audio) arrive
at the same path and are told apart by content type, and the upload's name,
suffix and playback choices come from headers rather than a body it could
parse. `/radio/retry` and `/radio/reprocess` are the same job dict built from
a song that is already here. All four answers are `202` with the job, so the
page can follow it through `/radio/jobs`.

The route functions take the handler as their first argument — the shape
`desktop_tools.get_status` already uses — and lean on it for `reply`,
`json_body` and `marked`, which stay with the server that defines the wire.
"""

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import request_guard
from radio_jobs import (
    DATA,
    JOBS,
    LOCK,
    cancel,
    catalog,
    cookie_audio_format,
    cookie_audio_quality,
    playback_format,
    playback_quality,
    rename,
    reprocess_job,
    submit,
    update,
)

UPLOAD_LIMIT = 100 * 1024 * 1024


def new_job(tid, source, title, split, audio_format, audio_quality, source_name):
    return {
        "id": tid,
        "source": source,
        "title": title,
        "split": split,
        "audio_format": audio_format,
        "audio_quality": audio_quality,
        "source_name": source_name,
        "phase": "Queued",
        "done": False,
        "error": None,
    }


def room_to_queue():
    """Checked before the job exists: refusing after the upload is written
    would leave the file it was refused for."""
    with LOCK:
        request_guard.queue_room(sum(1 for item in JOBS.values() if not item["done"]))


def upload_length(handler):
    """The declared body size, or None after refusing an oversize one."""
    length = int(handler.headers.get("Content-Length", 0))
    if length <= 0 or length > UPLOAD_LIMIT:
        handler.reply({"error": "Choose a file smaller than 100 MB."}, 413)
        return None
    return length


def playback_choices(handler, audio_format, audio_quality):
    cookie = handler.headers.get("Cookie")
    return (
        playback_format(audio_format or cookie_audio_format(cookie)),
        playback_quality(audio_quality or cookie_audio_quality(cookie)),
    )


def queue(handler, job):
    submit(job)
    handler.reply(job, 202)


def not_a_repeat(source):
    """A link that is already waiting, or already a song here, is refused by
    name: pasting a list twice should not prepare everything twice."""
    with LOCK:
        if any(j["source"] == source and not j["done"] for j in JOBS.values()):
            raise ValueError("That link is already in the queue.")
    for row in catalog():
        if row.get("source_url") == source:
            raise ValueError(
                f"That link is already in your library as “{row['title']}”. "
                "Use Change audio to prepare it again."
            )


def link_job(handler, tid, length):
    payload = json.loads(handler.rfile.read(length))
    source = payload.get("url", "").strip()
    parsed = urlsplit(source)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Paste a complete http or https link.")
    not_a_repeat(source)
    audio_format, audio_quality = playback_choices(
        handler, payload.get("audio_format"), payload.get("audio_quality")
    )
    title = str(payload.get("title", "")).strip()[:200]
    split = payload.get("split", True) is True
    return new_job(tid, source, title, split, audio_format, audio_quality, None)


def upload_job(handler, tid, length):
    name = Path(unquote(handler.headers.get("X-Filename", "song.mp3"))).name
    body = handler.rfile.read(length)
    ext = request_guard.upload_suffix(body[:128], Path(name).suffix.lower())
    # basename: the name written is one component, never a path.
    source = str(DATA / os.path.basename(tid + ext))
    with open(source, "wb") as upload:
        upload.write(body)
    audio_format, audio_quality = playback_choices(
        handler,
        handler.headers.get("X-Audio-Format"),
        handler.headers.get("X-Audio-Quality"),
    )
    title = Path(name).stem[:200]
    split = handler.headers.get("X-Split", "true") == "true"
    return new_job(tid, source, title, split, audio_format, audio_quality, name)


def post_import(handler):
    room_to_queue()
    length = upload_length(handler)
    if length is None:
        return
    tid = "radio_" + uuid4().hex[:12]
    if handler.headers.get("Content-Type", "").startswith(
        request_guard.CONTENT_TYPE_JSON
    ):
        job = link_job(handler, tid, length)
    else:
        # Raw bytes: any content type at all would do, so the marker
        # header is the only thing that keeps this off a foreign page.
        handler.marked()
        job = upload_job(handler, tid, length)
    with LOCK:
        JOBS[tid] = job
    queue(handler, job)


def post_retry(handler):
    payload = handler.json_body("Invalid retry request")
    room_to_queue()
    with LOCK:
        job = JOBS.get(payload.get("id"))
        if not job or not job["done"]:
            raise ValueError("That job is not available to retry.")
        update(job, done=False, phase="Queued", error=None, result=None)
        job.update(cancelled=False, finished_at=0, percent=None, detail="")
    queue(handler, job)


def post_reprocess(handler):
    payload = handler.json_body("Invalid reprocess request")
    room_to_queue()
    job = reprocess_job(
        str(payload.get("key") or ""),
        payload.get("audio_format") or "mp3",
        payload.get("audio_quality") or "standard",
        payload.get("split"),
    )
    with LOCK:
        if job["id"] in JOBS and not JOBS[job["id"]]["done"]:
            raise ValueError("That song is already being processed.")
        JOBS[job["id"]] = job
    queue(handler, job)


def post_cancel(handler):
    payload = handler.json_body("Invalid cancel request")
    handler.reply(cancel(str(payload.get("id") or "")))


def post_rename(handler):
    payload = handler.json_body("Invalid rename request")
    handler.reply(rename(str(payload.get("key") or ""), payload.get("title")))
