#!/usr/bin/env python3
"""THIRD-PARTY-NOTICES for everything the castle project ships.

    third_party_notices.py generate             write every notices file
    third_party_notices.py check                exit 1 if a file is stale, a
                                                lockfile moved past the
                                                inventory, a component
                                                breaks the licence policy,
                                                or LICENSE or a manifest of
                                                ours stops naming our own
                                                licence (MIT)
    third_party_notices.py refresh              re-read the desktop app's
                                                crates (cargo metadata,
                                                offline) and regenerate
    third_party_notices.py check-firmware DIR   hold an ESPHome build
                                                directory's linker maps to
                                                the firmware table
    third_party_notices.py path ARTIFACT        print one notices file's path

What ships, and where its notices go (docs/LICENSING.md has the why):

  firmware image      licenses/THIRD-PARTY-NOTICES-firmware.txt -> a release
                      asset beside the images, and the web flasher's
                      THIRD-PARTY-NOTICES.txt (tools/release_assets.py)
  desktop app         licenses/THIRD-PARTY-NOTICES-desktop.txt -> bundled as
                      a Tauri resource (desktop/src-tauri/tauri.conf.json)
  castle-core zips    licenses/THIRD-PARTY-NOTICES-castle-core.txt -> inside
                      each zip (tools/release_assets.py zip-core)
  source zip /        THIRD-PARTY-NOTICES.txt at the top of the tree, which
  installer bundle    the release's source zip carries as it is

The tables live in tools/notices_firmware.py (the image), notices_desktop.py
(the crates, the Rust standard library, the Windows installer) and
notices_external.py (what the installer downloads but nobody here ships);
the policy and the pinned licence texts in notices_model.py. Stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import notices_desktop
import notices_firmware
import notices_render
from notices_model import ROOT, policy_errors, shown, text_errors

#: Exit status for a check that found something.
FAILED = 1


def generate() -> list[Path]:
    """Write every notices file; return the paths written."""
    written = []
    for rel, text in notices_render.outputs().items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def problems() -> list[str]:
    """Everything `check` refuses, one line each."""
    errors = text_errors()
    errors += notices_desktop.staleness(notices_desktop.load())
    for key in notices_render.ARTIFACTS:
        errors += [f"{key}: {e}" for e in policy_errors(notices_render.components(key))]
    errors += notices_render.stale()
    errors += notices_render.own_licence_errors()
    return errors


def _report(errors: list[str], ok: str) -> int:
    for line in errors:
        print(f"  {line}", file=sys.stderr)
    if errors:
        print(f"third-party notices: {len(errors)} problem(s)", file=sys.stderr)
        return FAILED
    print(ok)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "generate" and not rest:
        for path in generate():
            print(shown(path.relative_to(ROOT)))
        return 0
    if cmd == "check" and not rest:
        return _report(problems(), "third-party notices: current")
    if cmd == "refresh" and not rest:
        doc = notices_desktop.refresh()
        notices_desktop.INVENTORY.write_text(
            notices_desktop.dump(doc), encoding="utf-8", newline="\n"
        )
        generate()
        return _report(problems(), "third-party notices: refreshed and current")
    if cmd == "check-firmware" and len(rest) == 1:
        errors = notices_firmware.check_build(Path(rest[0]))
        return _report(errors, f"firmware notices cover {shown(Path(rest[0]))}")
    if cmd == "path" and len(rest) == 1 and rest[0] in notices_render.ARTIFACTS:
        print(shown(ROOT / notices_render.ARTIFACTS[rest[0]].path))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
