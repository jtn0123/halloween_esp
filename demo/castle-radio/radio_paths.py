"""Where Castle Radio keeps its library.

`.radio-data/` beside the server, as it always was — unless
`CASTLE_RADIO_DATA` names another directory. The desktop app
(desktop/README.md) sets it to the per-user app-data dir, because a bundled
server lives in an install tree that is read-only, signed, or replaced by
the next update, and a buyer's songs must survive all three.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def data_dir() -> Path:
    explicit = os.environ.get("CASTLE_RADIO_DATA")
    return Path(explicit).expanduser() if explicit else HERE / ".radio-data"
