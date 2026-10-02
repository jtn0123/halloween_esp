"""Read-only desktop readiness endpoint; no installs or model downloads."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import device_bridge
import rich_show
from castle_tools_status import status


def get_status(handler, _parsed):
    """The probe checks metadata and cached files, without loading the model."""
    handler.reply({**status(), "castle_origin": "http://" + device_bridge.HOST})


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
