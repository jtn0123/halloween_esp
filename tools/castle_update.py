#!/usr/bin/env python3
"""Update the castle's firmware from a GitHub Release — when the owner asks.

    castle_update.py check  [--host H]    what the castle runs, what is newer
    castle_update.py update [--host H]    fetch, verify and flash it

Never automatic: Castle Radio's "Update castle" button calls `run`
(demo/castle-radio/castle_update_routes.py), and this command line is the
same function for someone at a terminal. One update, in order, and every
step can stop it with a sentence the owner can act on:

1. The castle says what it is — /api/status `version`, `board` and
   `fw_variant` (v5.74). One too old to name its board is not guessed at.
2. GitHub says what is newest on the owner's channel: stable, unless they
   opted in to pre-releases (tools/release_channel.py). One API call.
3. The release's descriptor for THAT board, `castle-fw-<board>-<tag>.json`
   (tools/release_assets.py), checked against the release's SHA256SUMS like
   every byte after it: no checksums, no update. Its build must be the
   castle's — a yard castle is never handed the buyer build, nor a buyer's
   the yard one — and its version newer than the castle's.
4. The OTA image the descriptor names: SHA256SUMS, its length, its magic.
5. A keyed castle is asked whether this computer holds its key BEFORE a
   megabyte crosses the Wi-Fi, as Castle Radio's sync does.
6. tools/sd_ota.py, the repo's one OTA client: `push` stops the audio and
   PUTs /api/ota; `wait_back` polls until a status comes from a NEW boot —
   uptime younger than the push — which is also the poll that confirms the
   image (v5.60) so the bootloader keeps it.
7. The verdict is the version that boot reports. The one asked for: done.
   The one it had: the new image did not start and ESP-IDF's rollback put
   the old one back, and the owner is told exactly that.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import castle_keys
import desktop_release as rel
import fw_formats
import hosts
import release_channel as channel
import sd_ota
import sd_sync

#: The release contract's names as an INSTALLED app looks for them: a
#: release that renames them strands every castle behind it, so they are
#: spelled here rather than imported, and tests/test_release_contract.py
#: holds them to what tools/release_assets.py stages.
ABOUT = "castle-fw-{board}-{tag}.json"
OTA = "castle-fw-{board}-{tag}.ota.bin"
ABOUT_SCHEMA = 1
#: What a castle from before v5.74 is: no fw_variant field, and the yard's.
OLD_VARIANT = "yard"
#: How long a castle gets to come back: TRIES polls EVERY seconds apart.
TRIES, EVERY = 40, 3.0

Say = Callable[[str], None]


class UpdateError(RuntimeError):
    """Why the castle was not updated, or did not take it — for the owner."""


@dataclass(frozen=True)
class Offer:
    """What the owner's channel has for one board: a release and its
    descriptor, already checked against the release's SHA256SUMS."""

    release: rel.Release
    about: Mapping[str, Any]

    @property
    def version(self) -> str:
        return str(self.about["version"])


@dataclass(frozen=True)
class Plan:
    """One castle measured against one offer — what the card shows."""

    current: str
    board: str
    variant: str
    available: str
    tag: str
    update: bool
    message: str


def castle_status(host: str) -> dict[str, Any]:
    try:
        st = json.loads(sd_sync.api(host, "GET", "/api/status", timeout=8))
    except (OSError, ValueError) as exc:
        raise UpdateError(
            f"The castle at {host} is not answering ({exc}) — is it switched "
            "on and on this Wi-Fi?"
        ) from None
    if not isinstance(st, dict) or not st.get("version"):
        raise UpdateError("The castle answered without saying which firmware it runs.")
    return st


def identity(st: Mapping[str, Any]) -> tuple[str, str, str]:
    """(version, board, build) — the three things an image must match."""
    board = str(st.get("board") or "")
    if not board:
        raise UpdateError(
            f"This castle runs firmware {st.get('version')}, too old to say which "
            "board it is — update it once over USB with the web flasher, and "
            "from then on from here."
        )
    return str(st["version"]), board, str(st.get("fw_variant") or OLD_VARIANT)


def offer(fetch: rel.Fetch, board: str, prerelease: bool, scratch: Path) -> Offer:
    """The newest release on the channel, and its verified descriptor for `board`."""
    try:
        release = channel.newest(fetch, prerelease)
        name = ABOUT.format(board=board, tag=release.tag)
        if name not in release.assets:
            raise UpdateError(
                f"Release {release.tag} has no firmware for this castle's board "
                f"({board}) — nothing to update to."
            )
        path = rel.fetch_verified_asset(release, name, scratch, fetch)
    except (rel.ReleaseError, OSError) as exc:
        raise UpdateError(f"Could not get the newest castle firmware: {exc}") from None
    try:
        about = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        about = None
    if not isinstance(about, dict) or about.get("schema") != ABOUT_SCHEMA:
        raise UpdateError(
            f"{name} is in a form this app does not read — update Castle Tools first."
        )
    if about.get("board") != board or about.get("tag") != release.tag:
        raise UpdateError(
            f"{name} describes firmware for {about.get('board')!r}, not this "
            f"castle's board ({board}) — not updating."
        )
    if fw_formats.parse_version(about.get("version")) is None:
        raise UpdateError(f"{name} names no firmware version — not updating.")
    return Offer(release, about)


def judge(st: Mapping[str, Any], off: Offer) -> Plan:
    """Update, or why not. Refuses (UpdateError) the image of another build."""
    current, board, variant = identity(st)
    tag, build = off.release.tag, str(off.about.get("fw_variant") or "")
    if build != variant:
        raise UpdateError(
            f"This castle runs the {variant} build of the firmware, and release "
            f"{tag} carries the {build} build — the app never swaps one for the "
            "other."
        )
    have = fw_formats.parse_version(current)
    new = fw_formats.parse_version(off.version)
    if have is not None and new is not None and have >= new:
        newest = "the newest there is" if have == new else f"newer than {tag}'s"
        message = f"The castle runs firmware {current}, {newest} — nothing to update."
        return Plan(current, board, variant, off.version, tag, False, message)
    message = f"Firmware {off.version} ({tag}) is ready — the castle runs {current}."
    return Plan(current, board, variant, off.version, tag, True, message)


def check_key(host: str, st: Mapping[str, Any]) -> None:
    """A keyed castle refuses the image without its key: say so first."""
    if not st.get("locked"):
        return
    key = hosts.castle_key(host)
    if not key:
        raise UpdateError(castle_keys.KEY_REQUIRED)
    try:
        code = castle_keys.ask(host, "/api/key", key)[0]
    except OSError as exc:
        raise UpdateError(f"The castle stopped answering ({exc}).") from None
    if code == 401:
        raise UpdateError(
            f"{castle_keys.KEY_REQUIRED} — the one this computer has is not its key."
        )


def download_image(off: Offer, board: str, scratch: Path, fetch: rel.Fetch) -> bytes:
    name = OTA.format(board=board, tag=off.release.tag)
    if off.about.get("ota") != name:
        raise UpdateError(
            f"The release names its image {off.about.get('ota')!r}, not {name} — "
            "not updating."
        )
    try:
        data = rel.fetch_verified_asset(off.release, name, scratch, fetch).read_bytes()
    except (rel.ReleaseError, OSError) as exc:
        raise UpdateError(
            f"The firmware download did not check out, so nothing was sent to "
            f"the castle: {exc}"
        ) from None
    if len(data) != off.about.get("ota_bytes") or data[:1] != b"\xe9":
        raise UpdateError(
            f"{name} is not the image its release describes — nothing was sent "
            "to the castle."
        )
    return data


def rolled_back(host: str, before: str, wanted: str) -> str:
    why = ""
    try:
        health = json.loads(sd_sync.api(host, "GET", "/api/health", timeout=5))
        if isinstance(health, dict) and health.get("last_reset"):
            why = f" (the castle's last restart: {health['last_reset']})"
    except (OSError, ValueError):
        pass
    return (
        f"The castle restarted but still runs firmware {before}: {wanted} did "
        "not start, so the castle went back to the firmware it had — the "
        f"rollback that keeps a bad update from breaking it{why}. Nothing is "
        "broken and the castle works as before. Try once more; if it happens "
        "again, use Report a problem on the castle's /owner page."
    )


def never_back(host: str, before: str, waited: int) -> str:
    """No fresh boot answered: the castle is either down or never restarted."""
    try:
        still = castle_status(host)
    except UpdateError:
        return (
            f"The castle has not come back after the update ({waited} s). If it "
            "stays silent, switch it off and on again: a castle whose new "
            "firmware cannot start goes back to the old one by itself."
        )
    return (
        f"The castle did not restart and still runs firmware {still['version']} "
        f"(it ran {before}): the new firmware never reached it in full, so "
        "nothing changed. Try again."
    )


def install(
    host: str,
    st: Mapping[str, Any],
    off: Offer,
    data: bytes,
    say: Say,
    tries: int = TRIES,
    every: float = EVERY,
) -> str:
    """Flash `data` and judge the boot that follows. Returns the success line."""
    before = str(st["version"])
    started = time.monotonic()
    say(f"Sending firmware {off.version} to the castle — it stops playing")
    try:
        reply = sd_ota.push(host, data, sd_sync.api)
    except sd_ota.Refused as err:
        raise UpdateError(
            f"The castle refused the new firmware ({err.code}: {err.reason}) and "
            f"still runs {before}."
        ) from None
    except SystemExit as exc:  # sd_sync.api's 401: the key sentence
        raise UpdateError(str(exc)) from None
    if reply is not None and reply.get("flashed") is not True:
        raise UpdateError(
            f"The castle did not take the new firmware ({json.dumps(reply)}) and "
            f"still runs {before}."
        )
    say("Waiting for the castle to restart")

    def new_boot(s: Mapping[str, Any]) -> bool:
        up = s.get("uptime_s")
        return not isinstance(up, int) or up <= time.monotonic() - started + 2

    back = sd_ota.wait_back(host, sd_sync.api, new_boot, tries, every)
    if back is None:
        raise UpdateError(never_back(host, before, round(tries * every)))
    now = str(back.get("version") or "")
    if fw_formats.parse_version(now) == fw_formats.parse_version(off.version):
        return f"The castle now runs firmware {now} (it ran {before})."
    if now == before:
        raise UpdateError(rolled_back(host, before, off.version))
    raise UpdateError(
        f"The castle came back running {now or 'no version it will name'}, not "
        f"{off.version} — check it with the web flasher."
    )


def check(host: str, fetch: rel.Fetch | None = None, prerelease: bool = False) -> Plan:
    """The card's question: what the castle runs and what is newer. `fetch`
    defaults to desktop_release.http_fetch as it is when called."""
    st = castle_status(host)
    with tempfile.TemporaryDirectory(prefix="castle-fw-") as tmp:
        board = identity(st)[1]
        return judge(st, offer(fetch or rel.http_fetch, board, prerelease, Path(tmp)))


def run(
    host: str,
    fetch: rel.Fetch | None = None,
    prerelease: bool = False,
    say: Say = print,
    offer_for: Callable[[str], Offer] | None = None,
    tries: int = TRIES,
    every: float = EVERY,
) -> str:
    """The whole update. Returns the outcome line — "nothing to update" is
    one — and raises UpdateError for everything that stopped it. Castle
    Radio passes `offer_for` to reuse the release it already looked up."""
    fetch = fetch or rel.http_fetch
    with tempfile.TemporaryDirectory(prefix="castle-fw-") as tmp:
        scratch = Path(tmp)
        say("Asking the castle what it runs")
        st = castle_status(host)
        board = identity(st)[1]
        say("Looking for new castle firmware")
        off = (
            offer_for(board) if offer_for else offer(fetch, board, prerelease, scratch)
        )
        plan = judge(st, off)
        if not plan.update:
            return plan.message
        check_key(host, st)
        say(f"Downloading firmware {off.version} and checking it")
        data = download_image(off, board, scratch, fetch)
        return install(host, st, off, data, say, tries, every)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("check", "update"))
    ap.add_argument(
        "--host",
        help="the castle: an IP, host:port or devices.toml name "
        "(default: CASTLE_HOST, then devices.toml)",
    )
    # The pre-release channel stays hidden here as everywhere: the flag is
    # the same opt-in as CASTLE_PRERELEASE=1, and help does not list it.
    ap.add_argument("--prerelease", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    host = hosts.resolve(args.host)
    early = args.prerelease or channel.opted_in()
    try:
        if args.command == "check":
            print(check(host, None, early).message)
        else:
            say: Say = lambda line: print(f"  {line} …")  # noqa: E731
            print(run(host, None, early, say))
    except UpdateError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
