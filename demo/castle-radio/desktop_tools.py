"""Read-only desktop readiness endpoint; no installs or model downloads.

Platform-specific answers (which installer, whether website startup exists
here) come from castle_tools_status, which decides them by sys.platform; the
page hides what this computer does not have (desktop-tools.js)."""

from pathlib import Path

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import device_bridge
import rich_show
from castle_tools_status import status
from diagnostics import app_version


def get_status(handler, _parsed):
    """The probe checks metadata and cached files, without loading the model.
    `app_version` is the release this copy came from, for the tools card.
    `castle_origin` must equal the castle page's own location.origin, which
    device-helper.js compares it with — and the castle speaks plain HTTP."""
    handler.reply(
        {
            **status(),
            "castle_origin": "http://" + device_bridge.HOST,  # NOSONAR — see above
            "app_version": app_version(),
        }
    )


def catalog(rows, library):
    """Give the device page the same mailbox-rate cues as a published song."""
    result = []
    for row in rows:
        name = row.get("playback_file") or f"{row['key']}.mp3"
        path = library / Path(name).name
        result.append(
            {
                **row,
                "frames": device_bridge.imported_light_frames(row.get("cues", [])),
                "prepared_show": rich_show.metadata(library, row),
                "filename": path.name,
                "bytes": path.stat().st_size if path.is_file() else 0,
            }
        )
    return result
