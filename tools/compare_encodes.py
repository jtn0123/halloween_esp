#!/usr/bin/env python3
"""Run one codec comparison for a caller that is not Python — the Rust
studio pipes {"path", "opts", "dest"} in as JSON and gets back exactly
what studio_media.compare computes before it decorates: the encode rows
(via codec_compare.encode_set) or the same one-line SystemExit error.
Keeping this in Python keeps the scores and the error strings in one
implementation whichever server is running.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import codec_compare as cc


def main() -> int:
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict):
        print(json.dumps({"ok": False, "error": "bad request"}))
        return 0
    opts = req.get("opts")
    if not isinstance(opts, dict):
        print(json.dumps({"ok": False, "error": "bad request"}))
        return 0
    try:
        # The encoders narrate to stdout ("note: opus cannot encode at
        # 44100 Hz…"); stdout is this shim's answer channel, so the
        # narration moves to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            rows = cc.encode_set(
                Path(str(req.get("path"))), Path(str(req.get("dest"))), opts
            )
    except SystemExit as e:  # ffmpeg said no
        print(json.dumps({"ok": False, "error": str(e)}))
        return 0
    print(json.dumps({"ok": True, "reference": cc.REFERENCE, "codecs": rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
