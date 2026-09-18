"""`sd_sync.py ota` — flash a firmware image over plain HTTP.

Split out of tools/sd_sync.py at the 500-line cap, on the seam the command
list already draws: everything else in that tool syncs the CARD, this one
replaces the code that reads it.

It is handed the HTTP helper and the repo root rather than importing them —
`sd_sync` is a SCRIPT as well as a module, so a back-import would be a cycle
resolved differently in the two cases (and, run as `__main__`, not resolved
at all). One `api` to mock, and it arrives as an argument.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
from pathlib import Path
from typing import Protocol


class Api(Protocol):
    """tools/sd_sync.api — one request to the castle."""

    def __call__(
        self,
        ip: str,
        method: str,
        path: str,
        body: bytes | None = ...,
        timeout: float = ...,
    ) -> bytes: ...


def flash(ip: str, args: list[str], api: Api, root: Path) -> int:
    if not args:
        # `make ota` NAMES the image (check_image.py --path) rather than
        # leaving this fallback to guess, and the reason is this glob: since
        # 2026-09-16 firmware/build_path.yaml has put every build tree on an
        # external volume, so looking under the checkout found either nothing
        # or a months-old binary from before the move. Kept as a last resort
        # for a hand-run `sd_sync.py ota` in a tree that does build locally
        # (CI rewrites build_path.yaml to exactly that layout), and it prefers
        # firmware.ota.bin for the same reason check_image does.
        cands = sorted(
            (
                p
                for pat in ("firmware.ota.bin", "firmware.bin")
                for p in root.glob(f"firmware/.esphome/build/**/{pat}")
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not cands:
            raise SystemExit(
                "no built image found under firmware/.esphome/build — pass a "
                "path, or use `make ota`, which names the image the build just "
                "wrote (tools/check_image.py --path)"
            )
        args = [str(cands[0])]
    bin_path = Path(args[0])
    data = bin_path.read_bytes()
    if data[:1] != b"\xe9":
        raise SystemExit(f"{bin_path} does not look like an app image (no 0xE9 magic)")
    # Stop audio before an OTA — the standing rule (CLAUDE.md): a decode
    # mid-flash competes for the same starved heap.
    with contextlib.suppress(OSError):
        api(ip, "POST", "/api/stop", timeout=5)
    print(
        f"  flashing {bin_path.name} ({len(data) // 1024} KB) over HTTP ...",
        end="",
        flush=True,
    )
    try:
        resp = json.loads(api(ip, "PUT", "/api/ota", data, timeout=180))
        print(" ok" if resp.get("flashed") else f" UNEXPECTED: {resp}")
    except urllib.error.HTTPError as err:
        # An ANSWER is not a lost reply: the castle is still up and said no.
        # "ota end failed" is an image for another chip or another flash
        # layout (the S2's build sent to a Feather S3, back when both builds
        # existed) — which used to read as "rebooting", then "up".
        reason = err.read().decode(errors="replace").strip()
        raise SystemExit(
            f" REFUSED ({err.code}: {reason}) — still running the old image. "
            "Is this build for the chip at that address?"
        ) from err
    except OSError:
        # The device reboots moments after the last byte lands; losing the
        # response race is normal, not failure. The status poll below is the
        # real verdict.
        print(" (no reply — device likely rebooting)")
    print("  waiting for the device to come back ...", end="", flush=True)
    import time

    for _ in range(30):
        time.sleep(3)
        try:
            st = json.loads(api(ip, "GET", "/api/status", timeout=3))
            print(f" up — v{st.get('version')} compiled {st.get('compiled')}")
            print(
                "  CONFIRMED by that very poll — since v5.60 the first"
                " /api/status a boot answers cancels the rollback"
            )
            return 0
        except OSError:
            print(".", end="", flush=True)
    print(
        " no answer after 90 s — if it stays down, the bootloader will"
        " roll back to the previous image on the next power cycle"
    )
    return 1
