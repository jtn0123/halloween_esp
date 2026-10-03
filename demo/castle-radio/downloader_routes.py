"""Update the downloader: Castle Radio's button for tools/ytdlp_update.py.

Websites change under yt-dlp every few weeks, and a link that stops
importing says "The downloader may be out of date — Update the downloader"
(import_reason.DOWNLOADER_OLD). This is the button. The update is queued on
the same one-worker pool the imports run on (radio_jobs.POOL): it waits for
the import ahead of it and is never swapped in under one. Nothing here runs
by itself — only a press of the button starts an update.

    GET  /radio/downloader         what an import would run now, and the
                                   update in flight or the last one
    POST /radio/downloader/update  queue an update (a JSON `{}`, like every
                                   POST route: the content type is the guard)

The fetch, the check against yt-dlp's SHA2-256SUMS and the swap are all
ytdlp_update's; this module only queues it and remembers how it went.
"""

import threading
import time
from pathlib import Path

# The sandbox first, then tools/ on the path.
import radio_env  # noqa: F401

# isort: split
import castle_tools_status
import exe_paths
import import_reason as ir
import radio_jobs
import ytdlp_update as yu

#: The update in flight, or the last one: `phase` is idle, waiting (behind
#: an import), updating, done or failed; a failure carries the owner's
#: sentence in `error` and ytdlp_update's own words in `error_detail`.
STATE: dict[str, object] = {"phase": "idle"}
LOCK = threading.Lock()
#: yt-dlp's answer to --version, per file and mtime: a standalone build
#: unpacks itself to answer, which is seconds, and the page asks often.
VERSIONS: dict[tuple[str, int], str] = {}


def current():
    with LOCK:
        return dict(STATE)


def settle(**values):
    with LOCK:
        STATE.update(values, finished_at=time.time())


def version(program):
    """ytdlp_update.run_version, remembered until the file changes."""
    try:
        key = (program, Path(program).stat().st_mtime_ns)
    except OSError:
        return yu.run_version(program)
    if key not in VERSIONS:
        VERSIONS[key] = yu.run_version(program)
    return VERSIONS[key]


def importing():
    """Is an import running or waiting? The update queues behind it."""
    with radio_jobs.LOCK:
        return any(not job.get("done") for job in radio_jobs.JOBS.values())


def run_update():
    """The queued half, on the pool's thread, between two imports."""
    with LOCK:
        STATE.update(phase="updating", started_at=time.time())
    home = exe_paths.downloader_dir()
    try:
        if home is None:
            raise yu.UpdateError(yu.NO_HOME, "CASTLE_DOWNLOADER_DIR is set but empty")
        got = yu.update(home)
    except yu.UpdateError as exc:
        settle(phase="failed", error=str(exc), error_detail=exc.detail)
    except Exception as exc:  # the pool would keep it; the page would wait on
        detail = f"{type(exc).__name__}: {exc}"
        settle(phase="failed", error=ir.GENERIC, error_detail=detail)
    else:
        settle(phase="done", version=got["version"], changed=got["changed"])
    finally:
        # The desk's tool checks (/radio/tools) name the copy they found.
        castle_tools_status.status.cache_clear()


def get_status(handler, _parsed):
    handler.reply({**yu.status(run=version), "update": current()})


def post_update(handler):
    """Queue one update; a second press while one is queued is the same one."""
    handler.json_body("Invalid update request")
    with LOCK:
        if STATE.get("phase") not in ("waiting", "updating"):
            STATE.clear()
            STATE.update(
                phase="waiting" if importing() else "updating", queued_at=time.time()
            )
            radio_jobs.POOL.submit(run_update)
        state = dict(STATE)
    handler.reply({"update": state}, 202)
