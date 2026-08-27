#!/usr/bin/env python3
"""Answer "may this scene block be spliced?" for a caller that is not
Python — the Rust studio (castle-core's `studio` bin) pipes the splice
request in as JSON and gets studio_scenes.check()'s verdict back, so the
validation strings the desk shows come from ONE implementation whichever
server is running. stdin: {"id", "yaml", "scenes"}; stdout: {"ok": true}
or {"body": <the 400 body>, "code": 400}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import studio_scenes as ss


def main() -> int:
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict):
        print(json.dumps({"body": {"error": "need id and yaml"}, "code": 400}))
        return 0
    bad = ss.check(Path(str(req.get("scenes") or "")), req)
    if bad is None:
        print(json.dumps({"ok": True}))
    else:
        print(json.dumps({"body": bad[0], "code": bad[1]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
